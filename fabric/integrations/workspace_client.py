"""
fabric.integrations.workspace_client
======================================
Thin httpx wrapper around the workspace-manager HTTP API
(POST /worktree, DELETE /worktree/{task_id}, GET /worktree/{task_id}).

Retries on 5xx with exponential back-off (max 3 attempts); raises
immediately on 4xx so callers can surface the cause quickly.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from pydantic import BaseModel, Field

try:
    from fabric.logs.sanitizer.handler import SanitizedFileHandler  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "fabric.logs.sanitizer is required but could not be imported. "
        "Install the fabric/logs/sanitizer package before using workspace_client."
    ) from exc

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5  # seconds; doubles each attempt


# ---------------------------------------------------------------------------
# Pydantic shapes for workspace-manager responses
# ---------------------------------------------------------------------------


class WorktreeCreated(BaseModel):
    task_id: str
    path: str
    env_path: str
    created_at: str


class WorktreeDeleted(BaseModel):
    task_id: str
    deleted: bool


class CapacityError(Exception):
    """Raised when the worktree pool is full (HTTP 409)."""

    def __init__(self, task_id: str, detail: str | None = None) -> None:
        self.task_id = task_id
        self.detail = detail
        super().__init__(
            f"Worktree pool at capacity for task {task_id!r}: {detail}"
        )


class WorkspaceClientError(Exception):
    """Generic workspace-manager error (non-retryable 4xx)."""


# ---------------------------------------------------------------------------
# WorkspaceClient
# ---------------------------------------------------------------------------


class WorkspaceClient:
    """Async client for the workspace-manager REST API.

    Args:
        base_url: Base URL of the workspace-manager (e.g. ``http://localhost:8080``).
        http_client: Optional pre-constructed :class:`httpx.AsyncClient` (mainly
            for testing). When ``None`` a new client is created on first use.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client if it was created internally."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_worktree(
        self,
        task_id: str,
        repo: str,
        base_ref: str = "main",
        topic: str = "ops",
    ) -> WorktreeCreated:
        """Create a new worktree via ``POST /worktree``.

        Retries transparently on 5xx responses.

        Raises:
            CapacityError: On HTTP 409 (pool full).
            WorkspaceClientError: On other 4xx responses.
            httpx.HTTPStatusError: On persistent 5xx after retries exhausted.
        """
        client = await self._get_client()
        payload = {
            "task_id": task_id,
            "repo": repo,
            "base_ref": base_ref,
            "topic": topic,
        }
        response = await self._with_retry(
            lambda: client.post("/worktree", json=payload)
        )
        self._raise_for_status(response, task_id)
        return WorktreeCreated.model_validate(response.json())

    async def delete_worktree(self, task_id: str) -> WorktreeDeleted:
        """Delete a worktree via ``DELETE /worktree/{task_id}``.

        Retries on 5xx; tolerates 404 (already gone) as success.

        Raises:
            WorkspaceClientError: On 4xx responses other than 404.
        """
        client = await self._get_client()
        response = await self._with_retry(
            lambda: client.delete(f"/worktree/{task_id}")
        )
        if response.status_code == 404:
            logger.warning("worktree %s not found on DELETE (already cleaned up)", task_id)
            return WorktreeDeleted(task_id=task_id, deleted=True)
        self._raise_for_status(response, task_id)
        return WorktreeDeleted.model_validate(response.json())

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def lease(
        self,
        task_id: str,
        repo: str,
        base_ref: str = "main",
        topic: str = "ops",
    ) -> AsyncIterator[str]:
        """Async context manager that leases a worktree and returns its path.

        The worktree is guaranteed to be deleted in ``__aexit__`` even if the
        body raises.

        Usage::

            async with client.lease("TASK-1", "mnemonic-core") as cwd:
                await spawn_agent(cwd=cwd)
        """
        info = await self.create_worktree(task_id, repo, base_ref, topic)
        logger.info("worktree leased: task_id=%s path=%s", task_id, info.path)
        try:
            yield info.path
        finally:
            try:
                await self.delete_worktree(task_id)
                logger.info("worktree released: task_id=%s", task_id)
            except Exception:
                logger.exception("failed to release worktree %s — manual cleanup required", task_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _with_retry(self, request_fn) -> httpx.Response:
        """Execute ``request_fn`` with exponential back-off on 5xx."""
        delay = _RETRY_BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            response: httpx.Response = await request_fn()
            if response.status_code < 500:
                return response
            logger.warning(
                "workspace-manager returned %s (attempt %d/%d); retrying in %.1fs",
                response.status_code,
                attempt,
                _MAX_RETRIES,
                delay,
            )
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
        # Last attempt also raised — return the last response to let the caller
        # decide; _raise_for_status will surface it.
        return response  # type: ignore[return-value]  # last iteration set it

    @staticmethod
    def _raise_for_status(response: httpx.Response, task_id: str) -> None:
        """Convert HTTP error codes to typed Python exceptions."""
        if response.is_success:
            return
        body = response.text[:256]
        if response.status_code == 409:
            raise CapacityError(task_id, detail=body)
        if 400 <= response.status_code < 500:
            raise WorkspaceClientError(
                f"workspace-manager returned {response.status_code} for task {task_id!r}: {body}"
            )
        response.raise_for_status()
