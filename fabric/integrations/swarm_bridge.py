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
   c. Agent IDs are collected; worktrees remain **alive** so running agents
      have their cwd available.
3. After all waves, :meth:`SwarmBridge.cleanup_wave` (or the higher-level
   :meth:`SwarmBridge.dispatch` which calls it automatically) issues
   ``DELETE /worktree/{task_id}`` for every leased worktree.

Worktree ownership contract
----------------------------
Worktree lifetime is a **swarm-level responsibility**, not a spawn-level one.
A spawned agent is still running and actively using its cwd directory after
``agent_spawn`` returns; deleting the directory immediately would corrupt the
agent.

The caller (molyanov skill) must therefore:

1. Call :meth:`dispatch` to obtain a :class:`DispatchResult` (containing
   ``leased_worktrees``).
2. Wait until the agents have finished their work (by whatever signal the
   ruflo swarm provides — topic message, webhook, poll).
3. Call :meth:`cleanup_worktrees` with the ``leased_worktrees`` set to
   release all worktrees.

Alternatively, :meth:`dispatch` accepts ``auto_cleanup=True`` (default
``False``) and calls :meth:`cleanup_worktrees` before returning.  This is
useful in tests and synchronous workflows where the dispatch call blocks
until agents complete.

Failure path
------------
When :meth:`_spawn_one` raises during the spawn phase (after a worktree was
already created), that specific worktree is deleted immediately — the agent
never started, so there is nothing running.  All *other* worktrees that were
successfully leased remain alive until ``cleanup_worktrees`` is called.

``asyncio.gather`` is called with ``return_exceptions=True`` so a single
task failure does not cancel sibling coroutines mid-flight.  After gather,
the first non-None exception is re-raised.

Capacity handling
-----------------
On HTTP 409 the bridge sleeps for ``capacity_wait_s`` seconds (with ±50 %
jitter) then retries once.  A :class:`~asyncio.Semaphore` limits the number
of concurrent ``create_worktree`` calls so the workspace-manager is not
hammered by a thundering herd.

Constraints
-----------
- Pool capacity is enforced by workspace-manager (HTTP 409 →
  :exc:`~fabric.integrations.workspace_client.WorkspaceCapacityError`).
  On 409 the bridge waits with jitter then retries once; if still full, the
  error is surfaced to the caller.
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
import random
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
    WorkspaceCapacityError,
    WorkspaceClient,
    WorkspaceClientError,
    WorkspaceError,
)
from fabric.integrations.ruflo_client import (
    AgentConfig,
    AgentHandle,
    RufloClient,
    RufloSpawnError,
    SwarmConfig,
    SwarmHandle,
)

# Keep the old name importable so existing code that catches CapacityError works.
CapacityError = WorkspaceCapacityError

logger = logging.getLogger(__name__)

_CAPACITY_RETRY_DELAY = 5.0  # base seconds to wait before retrying on 409
_MAX_CONCURRENT_CREATES = 8  # max simultaneous create_worktree calls


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
    """Summary of a completed feature dispatch.

    Attributes:
        swarm: The ruflo swarm handle returned by ``swarm_init``.
        agents: All agent handles produced across every wave.
        leased_worktrees: Mapping of task_id → worktree path for every
            worktree that was successfully created and NOT yet deleted.
            Pass this to :meth:`SwarmBridge.cleanup_worktrees` when agents
            finish.  Empty when ``auto_cleanup=True`` was used.
    """

    swarm: SwarmHandle
    agents: list[AgentHandle] = field(default_factory=list)
    leased_worktrees: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SwarmError
# ---------------------------------------------------------------------------


class SwarmError(Exception):
    """Base class for swarm-bridge-level errors.

    Attributes:
        system: Which sub-system failed (``"workspace"`` or ``"ruflo"``).
        context: Operator-visible detail dict.
    """

    def __init__(
        self,
        message: str,
        *,
        system: str = "unknown",
        context: dict | None = None,
    ) -> None:
        self.system = system
        self.context: dict = context or {}
        super().__init__(message)


# ---------------------------------------------------------------------------
# SwarmBridge
# ---------------------------------------------------------------------------


class SwarmBridge:
    """Adapter from molyanov ``/do-feature`` to ruflo swarm MCP.

    Args:
        workspace: A :class:`~fabric.integrations.workspace_client.WorkspaceClient`
            instance.
        ruflo: A :class:`~fabric.integrations.ruflo_client.RufloClient` instance.
        capacity_wait_s: Base seconds (before jitter) to wait before a single
            retry when the worktree pool is full (HTTP 409).  Defaults to
            :data:`_CAPACITY_RETRY_DELAY`.
        max_concurrent_creates: Maximum simultaneous ``create_worktree`` calls
            per wave to avoid thundering herd on the workspace-manager.
    """

    def __init__(
        self,
        workspace: WorkspaceClient,
        ruflo: RufloClient,
        capacity_wait_s: float = _CAPACITY_RETRY_DELAY,
        max_concurrent_creates: int = _MAX_CONCURRENT_CREATES,
    ) -> None:
        self._workspace = workspace
        self._ruflo = ruflo
        self._capacity_wait_s = capacity_wait_s
        self._create_sem = asyncio.Semaphore(max_concurrent_creates)

    async def dispatch(
        self,
        plan: FeaturePlan,
        *,
        auto_cleanup: bool = False,
    ) -> DispatchResult:
        """Dispatch a complete feature plan through the ruflo swarm.

        Calls ``swarm_init`` once, then processes each wave sequentially.
        Within each wave, all tasks are spawned concurrently via
        :meth:`_dispatch_wave`.

        Worktree lifetime
        ~~~~~~~~~~~~~~~~~
        By default (``auto_cleanup=False``) the created worktrees remain alive
        after this method returns; agents are still running and need their cwd.
        The caller is responsible for calling
        :meth:`cleanup_worktrees` once agents have finished.

        With ``auto_cleanup=True`` all worktrees are deleted before returning.
        This is only appropriate when you are certain agents complete
        synchronously (e.g. in tests).

        Args:
            plan: The feature plan to dispatch.
            auto_cleanup: If ``True``, delete all leased worktrees before
                returning.

        Returns:
            A :class:`DispatchResult` with the swarm handle, all agent handles,
            and the map of leased worktrees (empty if ``auto_cleanup=True``).

        Raises:
            WorkspaceCapacityError: If the worktree pool is persistently full.
            WorkspaceClientError: On non-retryable workspace-manager errors.
            RufloSpawnError: If an agent_spawn call fails.
            SwarmError: On swarm_init failure.
        """
        if not plan.waves:
            logger.info(
                "dispatch: feature_id=%s has no waves — nothing to do",
                plan.feature_id,
            )
            # Still initialise a swarm so the caller has a valid swarm_id.
            swarm = await self._ruflo.init_swarm(
                SwarmConfig(
                    feature_id=plan.feature_id,
                    task_count=1,  # swarm_init requires >= 1
                    memory_namespace=plan.memory_namespace,
                )
            )
            return DispatchResult(swarm=swarm)

        total_tasks = sum(len(wave) for wave in plan.waves)
        logger.info(
            "dispatch: feature_id=%s waves=%d total_tasks=%d",
            plan.feature_id,
            len(plan.waves),
            total_tasks,
        )

        try:
            swarm = await self._ruflo.init_swarm(
                SwarmConfig(
                    feature_id=plan.feature_id,
                    task_count=total_tasks,
                    memory_namespace=plan.memory_namespace,
                )
            )
        except Exception as exc:
            raise SwarmError(
                f"swarm_init failed for feature {plan.feature_id!r}: {exc}",
                system="ruflo",
                context={"feature_id": plan.feature_id},
            ) from exc

        all_agents: list[AgentHandle] = []
        all_leased: dict[str, str] = {}  # task_id -> path

        try:
            for wave_index, wave in enumerate(plan.waves):
                logger.info(
                    "dispatching wave %d/%d (%d tasks) for swarm %s",
                    wave_index + 1,
                    len(plan.waves),
                    len(wave),
                    swarm.swarm_id,
                )
                wave_agents, wave_leased = await self._dispatch_wave(
                    swarm, wave, plan.memory_namespace
                )
                all_agents.extend(wave_agents)
                all_leased.update(wave_leased)
        except Exception:
            # Something failed mid-dispatch; clean up all worktrees we own
            # before propagating.
            logger.warning(
                "dispatch failed for feature %s; cleaning up %d leased worktrees",
                plan.feature_id,
                len(all_leased),
            )
            await self.cleanup_worktrees(all_leased)
            raise

        logger.info(
            "feature %s complete: %d agents spawned, %d worktrees leased",
            plan.feature_id,
            len(all_agents),
            len(all_leased),
        )

        if auto_cleanup:
            await self.cleanup_worktrees(all_leased)
            all_leased = {}

        return DispatchResult(swarm=swarm, agents=all_agents, leased_worktrees=all_leased)

    async def cleanup_worktrees(self, leased: dict[str, str]) -> None:
        """Delete all worktrees in ``leased`` (task_id → path mapping).

        This is the counterpart to :meth:`dispatch`.  Call it once the agents
        that were spawned for a wave/feature have finished their work.

        Failures are logged but not re-raised so that a single orphaned
        worktree does not prevent others from being released.

        Args:
            leased: Mapping of ``task_id`` to ``path`` as returned in
                :attr:`DispatchResult.leased_worktrees`.
        """
        for task_id in list(leased):
            await self._safe_delete(task_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _dispatch_wave(
        self,
        swarm: SwarmHandle,
        tasks: Sequence[TaskSpec],
        memory_namespace: str,
    ) -> tuple[list[AgentHandle], dict[str, str]]:
        """Spawn all tasks in a single wave concurrently.

        Uses ``return_exceptions=True`` so a single task failure does not
        cancel sibling coroutines mid-flight.  After gather, failed worktrees
        (where spawn never started) are cleaned up, and the first exception is
        re-raised.

        Returns:
            A 2-tuple of (agent_handles, leased_worktrees_map).
        """
        results = await asyncio.gather(
            *[self._spawn_one(swarm, task, memory_namespace) for task in tasks],
            return_exceptions=True,
        )

        agents: list[AgentHandle] = []
        leased: dict[str, str] = {}
        first_exc: BaseException | None = None

        for item in results:
            if isinstance(item, BaseException):
                if first_exc is None:
                    first_exc = item
            else:
                handle, cwd = item
                agents.append(handle)
                leased[handle.task_id] = cwd

        if first_exc is not None:
            # Clean up successfully-spawned worktrees from this wave before
            # propagating the error (the dispatch() caller will clean up any
            # previously accumulated leases).
            await self.cleanup_worktrees(leased)
            raise first_exc

        return agents, leased

    async def _spawn_one(
        self,
        swarm: SwarmHandle,
        task: TaskSpec,
        memory_namespace: str,
    ) -> tuple[AgentHandle, str]:
        """Lease one worktree and spawn one agent.

        Returns:
            A 2-tuple of (AgentHandle, worktree_path).  The worktree remains
            alive after this call — the agent needs it.  The caller is
            responsible for cleanup.

        On spawn failure the worktree is deleted immediately (the agent never
        started, so nothing is running against that cwd).
        """
        cwd = await self._lease_worktree_with_capacity_retry(task)
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
            # Spawn failed — agent never started, cwd is not in use.
            logger.exception(
                "agent_spawn failed for task %s (swarm %s); deleting worktree",
                task.task_id,
                swarm.swarm_id,
            )
            await self._safe_delete(task.task_id)
            raise
        # Success — return handle AND cwd so the caller can track the lease.
        return handle, cwd

    async def _lease_worktree_with_capacity_retry(self, task: TaskSpec) -> str:
        """Create a worktree, retrying once after jittered sleep on 409.

        A semaphore limits concurrent ``create_worktree`` calls to avoid
        thundering herd on the workspace-manager.
        """
        async with self._create_sem:
            try:
                info = await self._workspace.create_worktree(
                    task_id=task.task_id,
                    repo=task.repo,
                    base_ref=task.base_ref,
                    topic=task.topic,
                )
                return info.path
            except WorkspaceCapacityError:
                jitter = random.uniform(0, self._capacity_wait_s * 0.5)
                wait = self._capacity_wait_s + jitter
                logger.warning(
                    "worktree pool full for task %s; waiting %.2fs (base=%.1f jitter=%.2f) before retry",
                    task.task_id,
                    wait,
                    self._capacity_wait_s,
                    jitter,
                )
                await asyncio.sleep(wait)
                # Second attempt — let WorkspaceCapacityError propagate if still full.
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
