"""
workspace-manager FastAPI application.

Implements tech-spec §2.5 API contract exactly:
    POST   /worktree              — create
    DELETE /worktree/{task_id}    — cleanup
    GET    /worktree/{task_id}    — lookup
    GET    /worktrees             — list
    GET    /health                — introspection

Capacity cap = 10 (D10). Binds to BIND_IP env var (never 0.0.0.0).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from config import Settings, get_settings
from logging_setup import configure_logging
from models import (
    CreateWorktreeRequest,
    CreateWorktreeResponse,
    DeleteWorktreeResponse,
    ErrorResponse,
    HealthResponse,
    WorktreeInfo,
    WorktreeListResponse,
)
from state import AlreadyExistsError, NotFoundError, StateStore
from vault import VaultUnreachableError, check_reachable, materialise_env, remove_env
from worktree import WorktreeError, create_worktree, destroy_worktree, worktree_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_file, settings.log_level)
    logger.info(
        "workspace-manager starting",
        extra={"bind_ip": settings.bind_ip, "bind_port": settings.bind_port},
    )
    yield
    logger.info("workspace-manager shutdown")


app = FastAPI(
    title="workspace-manager",
    version="1.0.0",
    description="Per-task git worktree lifecycle + Vaultwarden .env materialisation",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_state_store(settings: Annotated[Settings, Depends(get_settings)]) -> StateStore:
    return StateStore(settings.state_file)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_task_id_path_param(task_id: str) -> str:
    """
    Re-validate task_id coming in as a path parameter.
    FastAPI route matching already limits characters, but we apply the strict
    regex here to block any path traversal attempts (e.g. task_id='../foo').
    """
    import re
    if not re.fullmatch(r"[A-Z0-9\-]{3,40}", task_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_task_id", "detail": "task_id must match ^[A-Z0-9-]{3,40}$"},
        )
    return task_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post(
    "/worktree",
    response_model=CreateWorktreeResponse,
    status_code=200,
    responses={
        409: {"model": ErrorResponse, "description": "Capacity exceeded or task already exists"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
async def post_worktree(
    body: CreateWorktreeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[StateStore, Depends(get_state_store)],
) -> CreateWorktreeResponse:
    """Create a git worktree and materialise per-worktree .env from Vaultwarden."""

    # Capacity check (pre-lock, advisory — the store.add() call is the authoritative gate)
    if store.count() >= settings.capacity_total:
        raise HTTPException(
            status_code=409,
            detail={"error": "capacity", "detail": f"Maximum {settings.capacity_total} worktrees reached"},
        )

    target = worktree_path(body.task_id, settings.worktrees_root, body.repo)
    env_path = target / ".env"
    created_at = datetime.now(timezone.utc)

    info = WorktreeInfo(
        task_id=body.task_id,
        repo=body.repo,
        base_ref=body.base_ref,
        topic=body.topic,
        path=str(target),
        env_path=str(env_path),
        created_at=created_at,
        status="active",
    )

    # Atomically register in state first.  Concurrent POST for same task_id
    # will hit AlreadyExistsError here (flock ensures mutual exclusion).
    try:
        store.add(info)
    except AlreadyExistsError:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_exists", "detail": f"task_id {body.task_id!r} already registered"},
        )

    # Materialise .env from Vaultwarden
    try:
        actual_env = await asyncio.to_thread(
            materialise_env,
            body.task_id,
            body.topic,
            target,
            settings.bw_session_credential,
        )
    except VaultUnreachableError as exc:
        # Roll back state entry on vault failure
        try:
            store.remove(body.task_id)
        except NotFoundError:
            pass
        logger.error("vault unreachable during POST /worktree", extra={"task_id": body.task_id})
        raise HTTPException(
            status_code=409,
            detail={"error": "vault_unreachable", "detail": str(exc)},
        )

    # Create git worktree (best-effort; vault already succeeded)
    try:
        await asyncio.to_thread(
            create_worktree,
            body.task_id,
            body.repo,
            body.base_ref,
            settings.repos_root,
            settings.worktrees_root,
            settings.sccache_dir,
        )
    except WorktreeError as exc:
        # Roll back: remove .env and state
        remove_env(actual_env)
        try:
            store.remove(body.task_id)
        except NotFoundError:
            pass
        logger.error("git worktree create failed", extra={"task_id": body.task_id, "error": str(exc)})
        raise HTTPException(
            status_code=500,
            detail={"error": "worktree_create_failed", "detail": str(exc)},
        )

    logger.info("POST /worktree succeeded", extra={"task_id": body.task_id})
    return CreateWorktreeResponse(
        task_id=body.task_id,
        path=str(target),
        env_path=str(actual_env),
        created_at=created_at,
    )


@app.delete(
    "/worktree/{task_id}",
    status_code=204,
    responses={
        404: {"model": ErrorResponse, "description": "task_id not found"},
        400: {"model": ErrorResponse, "description": "Invalid task_id"},
    },
)
async def delete_worktree(
    task_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[StateStore, Depends(get_state_store)],
) -> None:
    """Delete a worktree, its .env file, and remove from state."""
    task_id = _validate_task_id_path_param(task_id)

    try:
        info = store.get(task_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "detail": f"task_id {task_id!r} not registered"},
        )

    # Remove .env
    remove_env(Path(info.env_path))

    # Remove git worktree (best-effort)
    await asyncio.to_thread(
        destroy_worktree,
        task_id,
        info.repo,
        settings.repos_root,
        settings.worktrees_root,
    )

    # Remove from state
    try:
        store.remove(task_id)
    except NotFoundError:
        pass  # already gone; idempotent

    logger.info("DELETE /worktree succeeded", extra={"task_id": task_id})
    return None  # 204 No Content


@app.get(
    "/worktree/{task_id}",
    response_model=WorktreeInfo,
    responses={
        404: {"model": ErrorResponse, "description": "task_id not found"},
        400: {"model": ErrorResponse, "description": "Invalid task_id"},
    },
)
async def get_worktree(
    task_id: str,
    store: Annotated[StateStore, Depends(get_state_store)],
) -> WorktreeInfo:
    """Look up a single worktree by task_id."""
    task_id = _validate_task_id_path_param(task_id)
    try:
        return store.get(task_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "detail": f"task_id {task_id!r} not registered"},
        )


@app.get(
    "/worktrees",
    response_model=WorktreeListResponse,
)
async def list_worktrees(
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[StateStore, Depends(get_state_store)],
) -> WorktreeListResponse:
    """List all active worktrees."""
    all_items = store.list_all()
    return WorktreeListResponse(
        worktrees=all_items,
        count=len(all_items),
        capacity_used=len(all_items),
        capacity_total=settings.capacity_total,
    )


@app.get(
    "/health",
    response_model=HealthResponse,
)
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[StateStore, Depends(get_state_store)],
) -> HealthResponse:
    """
    Service introspection.

    sccache_mounted: True if SCCACHE_DIR exists on disk.
    vault_reachable: True if bw CLI can be reached and session is unlocked.
    Service stays up even when either subsystem is degraded.
    """
    sccache_mounted = settings.sccache_dir.exists()
    vault_reachable = await asyncio.to_thread(
        check_reachable,
        settings.bw_session_credential,
    )
    capacity_used = store.count()
    ok = sccache_mounted and vault_reachable

    return HealthResponse(
        ok=ok,
        sccache_mounted=sccache_mounted,
        vault_reachable=vault_reachable,
        capacity_used=capacity_used,
        capacity_total=settings.capacity_total,
    )
