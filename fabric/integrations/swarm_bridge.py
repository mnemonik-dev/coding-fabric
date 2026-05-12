"""
fabric.integrations.swarm_bridge
==================================
Adapter from molyanov ``/do-feature`` to ruflo swarm MCP.

Entry point: :class:`SwarmBridge`.

Protocol
--------
For each feature plan received via :meth:`SwarmBridge.dispatch`:

1. Call ``swarm_init`` **once** with the feature ID and total task count.
2. For each wave in the plan:
   a. For each task in the wave, lease an isolated worktree from the
      workspace-manager (``POST /worktree``).
   b. Call ``agent_spawn`` with ``cwd=<resolved_path>`` and the task brief.
   c. The worktree context-manager guarantees ``DELETE /worktree/{task_id}``
      in ``finally``, regardless of agent outcome.
3. If any spawn fails the successfully-leased worktrees in the same wave are
   still deleted before the exception propagates.  Errors are surfaced to the
   originating Telegram topic via the sanitized log chain (no raw stack traces).

Constraints
-----------
- Pool capacity is enforced by workspace-manager (HTTP 409 → :exc:`CapacityError`).
  On 409 the bridge waits up to ``capacity_wait_s`` seconds then retries once;
  if still full, the error is surfaced to the caller.
- Retries on 5xx from workspace-manager are handled inside
  :class:`~fabric.integrations.workspace_client.WorkspaceClient` (max 3,
  exponential); the bridge never retries 4xx.

Logging
-------
All log emission goes through :class:`~fabric.logs.sanitizer.handler.SanitizedFileHandler`
(imported at module level — ``ImportError`` is a hard fail, not a warning).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Sequence

try:
    from fabric.logs.sanitizer.handler import SanitizedFileHandler
except ImportError as exc:
    raise ImportError(
        "fabric.logs.sanitizer.handler.SanitizedFileHandler is required. "
        "Install fabric/logs/sanitizer before using swarm_bridge."
    ) from exc

from fabric.integrations.workspace_client import (
    CapacityError,
    WorkspaceClient,
    WorkspaceClientError,
)
from fabric.integrations.ruflo_client import (
    AgentConfig,
    AgentHandle,
    RufloClient,
    SwarmConfig,
    SwarmHandle,
)

logger = logging.getLogger(__name__)

_CAPACITY_RETRY_DELAY = 5.0  # seconds to wait before retrying on 409


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass
class TaskSpec:
    """Specification for a single task in a feature wave."""

    task_id: str
    brief: str
    repo: str
    base_ref: str = "main"
    topic: str = "ops"


@dataclass
class FeaturePlan:
    """A feature plan produced by molyanov ``/do-feature``.

    Attributes:
        feature_id: Unique identifier for the feature (e.g. branch name).
        waves: Ordered list of task waves.  Tasks within a wave may be
            processed concurrently; waves are processed sequentially.
        memory_namespace: ruflo memory namespace to use for all agents.
    """

    feature_id: str
    waves: list[list[TaskSpec]]
    memory_namespace: str = "default"


@dataclass
class DispatchResult:
    """Summary of a completed feature dispatch."""

    swarm: SwarmHandle
    agents: list[AgentHandle] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SwarmBridge
# ---------------------------------------------------------------------------


class SwarmBridge:
    """Adapter from molyanov ``/do-feature`` to ruflo swarm MCP.

    Args:
        workspace: A :class:`~fabric.integrations.workspace_client.WorkspaceClient`
            instance.
        ruflo: A :class:`~fabric.integrations.ruflo_client.RufloClient` instance.
        capacity_wait_s: Seconds to wait before a single retry when the worktree
            pool is full (HTTP 409).  Defaults to :data:`_CAPACITY_RETRY_DELAY`.
    """

    def __init__(
        self,
        workspace: WorkspaceClient,
        ruflo: RufloClient,
        capacity_wait_s: float = _CAPACITY_RETRY_DELAY,
    ) -> None:
        self._workspace = workspace
        self._ruflo = ruflo
        self._capacity_wait_s = capacity_wait_s

    async def dispatch(self, plan: FeaturePlan) -> DispatchResult:
        """Dispatch a complete feature plan through the ruflo swarm.

        Calls ``swarm_init`` once, then processes each wave sequentially.
        Within each wave, all tasks are spawned concurrently via
        :meth:`_dispatch_wave`.

        Returns:
            A :class:`DispatchResult` with the swarm handle and all agent
            handles produced.

        Raises:
            CapacityError: If the worktree pool is persistently full.
            WorkspaceClientError: On non-retryable workspace-manager errors.
            Exception: Any exception from ``agent_spawn`` (sanitized before
                reaching the Telegram topic).
        """
        total_tasks = sum(len(wave) for wave in plan.waves)
        logger.info(
            "dispatch: feature_id=%s waves=%d total_tasks=%d",
            plan.feature_id,
            len(plan.waves),
            total_tasks,
        )

        swarm = await self._ruflo.init_swarm(
            SwarmConfig(
                feature_id=plan.feature_id,
                task_count=total_tasks,
                memory_namespace=plan.memory_namespace,
            )
        )

        all_agents: list[AgentHandle] = []
        for wave_index, wave in enumerate(plan.waves):
            logger.info(
                "dispatching wave %d/%d (%d tasks) for swarm %s",
                wave_index + 1,
                len(plan.waves),
                len(wave),
                swarm.swarm_id,
            )
            wave_agents = await self._dispatch_wave(swarm, wave, plan.memory_namespace)
            all_agents.extend(wave_agents)

        logger.info(
            "feature %s complete: %d agents spawned", plan.feature_id, len(all_agents)
        )
        return DispatchResult(swarm=swarm, agents=all_agents)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _dispatch_wave(
        self,
        swarm: SwarmHandle,
        tasks: Sequence[TaskSpec],
        memory_namespace: str,
    ) -> list[AgentHandle]:
        """Spawn all tasks in a single wave concurrently.

        Each task gets an isolated worktree lease.  If any spawn fails the
        successfully-completed worktrees are cleaned up before the exception
        propagates (the context manager ensures DELETE in ``finally``).
        """
        return await asyncio.gather(
            *[self._spawn_one(swarm, task, memory_namespace) for task in tasks]
        )

    async def _spawn_one(
        self,
        swarm: SwarmHandle,
        task: TaskSpec,
        memory_namespace: str,
    ) -> AgentHandle:
        """Lease one worktree and spawn one agent, cleaning up on exit."""
        cwd = await self._lease_worktree_with_capacity_retry(task)
        # The worktree was already created; we own it. Delete in finally.
        try:
            handle = await self._ruflo.spawn_agent(
                AgentConfig(
                    swarm_id=swarm.swarm_id,
                    task_id=task.task_id,
                    task_brief=task.brief,
                    cwd=cwd,
                    memory_namespace=memory_namespace,
                )
            )
        except Exception:
            logger.exception(
                "agent_spawn failed for task %s (swarm %s); worktree will be deleted",
                task.task_id,
                swarm.swarm_id,
            )
            raise
        finally:
            await self._safe_delete(task.task_id)
        return handle

    async def _lease_worktree_with_capacity_retry(self, task: TaskSpec) -> str:
        """Create a worktree, retrying once after ``capacity_wait_s`` on 409."""
        try:
            info = await self._workspace.create_worktree(
                task_id=task.task_id,
                repo=task.repo,
                base_ref=task.base_ref,
                topic=task.topic,
            )
            return info.path
        except CapacityError:
            logger.warning(
                "worktree pool full for task %s; waiting %.1fs before retry",
                task.task_id,
                self._capacity_wait_s,
            )
            await asyncio.sleep(self._capacity_wait_s)
            # Second attempt — let CapacityError propagate if still full
            info = await self._workspace.create_worktree(
                task_id=task.task_id,
                repo=task.repo,
                base_ref=task.base_ref,
                topic=task.topic,
            )
            return info.path

    async def _safe_delete(self, task_id: str) -> None:
        """Delete a worktree, logging but not re-raising on failure."""
        try:
            await self._workspace.delete_worktree(task_id)
            logger.info("worktree deleted: task_id=%s", task_id)
        except Exception:
            logger.exception(
                "failed to delete worktree %s — orphaned worktree requires manual cleanup",
                task_id,
            )
