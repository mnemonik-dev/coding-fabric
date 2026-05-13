"""
fabric.integrations.ruflo_client
==================================
Thin async wrapper around the ruflo swarm MCP tools ``swarm_init`` and
``agent_spawn``.

In production these tools are invoked through the ruflo MCP server; for
the purposes of this module they are represented as async callables that
can be injected at construction time, making them trivially mockable in
tests.

The production wiring (connecting to the real ruflo MCP process) is
handled by the ``fabric-services`` Ansible role at deploy time — the
client only requires that the callable contract is satisfied.

Shapes:
    SwarmConfig     — arguments for swarm_init
    AgentConfig     — arguments for agent_spawn
    SwarmHandle     — result of swarm_init (contains swarm_id)
    AgentHandle     — result of agent_spawn (contains agent_id)

Exception hierarchy
-------------------
RufloError
  RufloSpawnError  — agent_spawn MCP call failed
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

try:
    from fabric.logs.sanitizer.handler import SanitizedFileHandler  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "fabric.logs.sanitizer is required. "
        "Install fabric/logs/sanitizer before using ruflo_client."
    ) from exc

logger = logging.getLogger(__name__)

# Type alias for injectable MCP tool callables
McpCallable = Callable[..., Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class RufloError(Exception):
    """Base class for all ruflo MCP errors."""


class RufloSpawnError(RufloError):
    """Raised when agent_spawn fails.

    Attributes:
        task_id: The task for which the spawn was attempted.
        swarm_id: The owning swarm.
        cause: The underlying exception, if any.
    """

    def __init__(
        self,
        task_id: str,
        swarm_id: str,
        cause: BaseException | None = None,
    ) -> None:
        self.task_id = task_id
        self.swarm_id = swarm_id
        self.cause = cause
        super().__init__(
            f"agent_spawn failed for task {task_id!r} in swarm {swarm_id!r}"
            + (f": {cause}" if cause else "")
        )


# ---------------------------------------------------------------------------
# Pydantic shapes for ruflo MCP tool inputs/outputs
# ---------------------------------------------------------------------------


class SwarmConfig(BaseModel):
    feature_id: str = Field(..., description="Unique feature/branch identifier")
    task_count: int = Field(..., ge=1, description="Total number of tasks in the feature")
    memory_namespace: str = Field(default="default")


class AgentConfig(BaseModel):
    swarm_id: str
    task_id: str
    task_brief: str
    cwd: str = Field(..., description="Absolute path to the agent's worktree")
    memory_namespace: str = Field(default="default")


class SwarmHandle(BaseModel):
    swarm_id: str
    feature_id: str
    task_count: int


class AgentHandle(BaseModel):
    agent_id: str
    swarm_id: str
    task_id: str
    cwd: str


# ---------------------------------------------------------------------------
# RufloClient
# ---------------------------------------------------------------------------


class RufloClient:
    """Async facade over the ruflo ``swarm_init`` and ``agent_spawn`` MCP tools.

    Args:
        swarm_init_fn: Async callable matching the ``swarm_init`` MCP tool
            signature. Receives keyword args matching :class:`SwarmConfig`.
        agent_spawn_fn: Async callable matching the ``agent_spawn`` MCP tool
            signature. Receives keyword args matching :class:`AgentConfig`.
    """

    def __init__(
        self,
        swarm_init_fn: McpCallable,
        agent_spawn_fn: McpCallable,
    ) -> None:
        self._swarm_init = swarm_init_fn
        self._agent_spawn = agent_spawn_fn

    async def init_swarm(self, config: SwarmConfig) -> SwarmHandle:
        """Call ``swarm_init`` once per feature.

        Returns:
            A :class:`SwarmHandle` containing the new ``swarm_id``.
        """
        logger.info(
            "swarm_init: feature_id=%s task_count=%d namespace=%s",
            config.feature_id,
            config.task_count,
            config.memory_namespace,
        )
        result = await self._swarm_init(
            feature_id=config.feature_id,
            task_count=config.task_count,
            memory_namespace=config.memory_namespace,
        )
        handle = SwarmHandle(
            swarm_id=result["swarm_id"],
            feature_id=config.feature_id,
            task_count=config.task_count,
        )
        logger.info("swarm created: swarm_id=%s", handle.swarm_id)
        return handle

    async def spawn_agent(self, config: AgentConfig) -> AgentHandle:
        """Call ``agent_spawn`` for a single task.

        The ``cwd`` field carries the absolute worktree path resolved
        from the workspace-manager lease.

        Returns:
            An :class:`AgentHandle` containing the new ``agent_id``.

        Raises:
            RufloSpawnError: Wraps any exception raised by ``agent_spawn_fn``,
                adding structured context (task_id, swarm_id).
        """
        logger.info(
            "agent_spawn: swarm_id=%s task_id=%s cwd=%s",
            config.swarm_id,
            config.task_id,
            config.cwd,
        )
        try:
            result = await self._agent_spawn(
                swarm_id=config.swarm_id,
                task_id=config.task_id,
                task_brief=config.task_brief,
                cwd=config.cwd,
                memory_namespace=config.memory_namespace,
            )
        except Exception as exc:
            raise RufloSpawnError(
                task_id=config.task_id,
                swarm_id=config.swarm_id,
                cause=exc,
            ) from exc
        handle = AgentHandle(
            agent_id=result["agent_id"],
            swarm_id=config.swarm_id,
            task_id=config.task_id,
            cwd=config.cwd,
        )
        logger.info(
            "agent spawned: agent_id=%s cwd=%s", handle.agent_id, handle.cwd
        )
        return handle
