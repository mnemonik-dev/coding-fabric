"""Long-running poll loop: Kaneo → dispatch → workspace + engine run.

Designed per openai/symphony SPEC.md §3.1 — a single asyncio task owns
the poll tick, the in-flight set, and concurrency caps. Restart-safe
without a persistent DB: Kaneo IS the durable state. On startup the
loop rebuilds its view by listing active tickets again.

Lifecycle is owned by FastAPI's ``lifespan`` (see main.py).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from auto_merge import MergePolicy, policy_from_env_or_default, schedule_or_tick
from dispatch import dispatch_ticket
from kaneo_client import KaneoClient, Ticket

logger = logging.getLogger(__name__)


class PollLoopConfig:
    """Runtime config sourced from env (no pydantic — single consumer)."""

    def __init__(self) -> None:
        self.enabled: bool = os.environ.get("SYMPHONY_POLL_ENABLED", "0") == "1"
        self.poll_interval_seconds: float = float(
            os.environ.get("SYMPHONY_POLL_INTERVAL_SECONDS", "30")
        )
        self.max_concurrent: int = int(os.environ.get("SYMPHONY_MAX_CONCURRENT", "3"))
        # Repo root used to load .symphony/workflows/<stage>.md. For now
        # Symphony watches a single repo at a time — multi-repo wiring
        # arrives when Kaneo's project_id → repo_root mapping is real.
        self.repo_root: Path = Path(
            os.environ.get("SYMPHONY_REPO_ROOT", "/opt/code/coding-fabric")
        )
        self.workspace_root: Path = Path(
            os.environ.get("SYMPHONY_WORKSPACE_ROOT", "/home/op/code/symphony-workspaces")
        )
        # Kaneo project Symphony watches. Empty string = scan all projects
        # the bearer can see (handled by poll_loop).
        self.project_id: str = os.environ.get("SYMPHONY_KANEO_PROJECT_ID", "")
        # Ticket statuses we treat as active (case-insensitive match).
        active_csv = os.environ.get(
            "SYMPHONY_ACTIVE_STATUSES", "to-do,in-progress,review,qa,ready-to-merge"
        )
        self.active_statuses: frozenset[str] = frozenset(
            s.strip().lower() for s in active_csv.split(",") if s.strip()
        )
        self.merge_policy: MergePolicy = policy_from_env_or_default()


async def _list_active_tickets(kaneo: KaneoClient, cfg: PollLoopConfig) -> list[Ticket]:
    if cfg.project_id:
        tickets = await kaneo.list_tasks_for_project(cfg.project_id)
    else:
        # Scan all workspaces × projects the bearer can see.
        tickets = []
        try:
            workspaces = await kaneo.list_workspaces()
        except Exception:
            logger.exception("poll_loop: list_workspaces failed")
            return []
        for ws in workspaces:
            ws_id = str(ws.get("id") or "")
            if not ws_id:
                continue
            try:
                projects = await kaneo.list_projects(ws_id)
            except Exception:
                logger.exception("poll_loop: list_projects(%s) failed", ws_id)
                continue
            for proj in projects:
                proj_id = str(proj.get("id") or "")
                if not proj_id:
                    continue
                try:
                    tickets.extend(await kaneo.list_tasks_for_project(proj_id))
                except Exception:
                    logger.exception("poll_loop: list_tasks_for_project(%s) failed", proj_id)
    return [t for t in tickets if t.status.lower() in cfg.active_statuses]


class PollLoop:
    """Wraps the asyncio task + in-flight tracking."""

    def __init__(self, kaneo: KaneoClient, cfg: PollLoopConfig) -> None:
        self._kaneo = kaneo
        self._cfg = cfg
        self._task: asyncio.Task[None] | None = None
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._sem = asyncio.Semaphore(cfg.max_concurrent)
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        cfg = self._cfg
        if not cfg.enabled:
            logger.info("poll_loop: SYMPHONY_POLL_ENABLED!=1, not starting")
            return
        cfg.workspace_root.mkdir(parents=True, exist_ok=True)
        logger.info(
            "poll_loop: starting (interval=%ss, max_concurrent=%s, repo_root=%s)",
            cfg.poll_interval_seconds,
            cfg.max_concurrent,
            cfg.repo_root,
        )
        self._task = asyncio.create_task(self._run(), name="symphony-poll")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        cfg = self._cfg
        while not self._stop_event.is_set():
            try:
                tickets = await _list_active_tickets(self._kaneo, cfg)
                logger.info("poll_loop: tick — %d active ticket(s)", len(tickets))
                for ticket in tickets:
                    if ticket.id in self._in_flight:
                        continue
                    self._in_flight[ticket.id] = asyncio.create_task(
                        self._dispatch_with_semaphore(ticket)
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("poll_loop: tick failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=cfg.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _dispatch_with_semaphore(self, ticket: Ticket) -> None:
        async with self._sem:
            try:
                if ticket.status.lower() == "ready-to-merge":
                    outcome = await schedule_or_tick(
                        ticket,
                        kaneo=self._kaneo,
                        policy=self._cfg.merge_policy,
                        repo_root=self._cfg.repo_root,
                    )
                    logger.info(
                        "poll_loop: auto_merge ticket=%s outcome=%s",
                        ticket.id,
                        outcome,
                    )
                else:
                    await dispatch_ticket(
                        ticket,
                        kaneo=self._kaneo,
                        repo_root=self._cfg.repo_root,
                        workspace_root=self._cfg.workspace_root,
                    )
            finally:
                self._in_flight.pop(ticket.id, None)
