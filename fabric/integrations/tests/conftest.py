"""
Shared pytest fixtures for fabric.integrations tests.

All fixtures use mock objects — no real workspace-manager or ruflo process
is required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import AsyncIterator

import pytest

from fabric.integrations.workspace_client import WorkspaceClient, WorktreeCreated, WorktreeDeleted
from fabric.integrations.ruflo_client import (
    AgentHandle,
    RufloClient,
    SwarmHandle,
)
from fabric.integrations.swarm_bridge import FeaturePlan, SwarmBridge, TaskSpec


# ---------------------------------------------------------------------------
# Worktree counter helper (tracks active leases for leak detection)
# ---------------------------------------------------------------------------


class WorktreeTracker:
    """Records created and deleted task IDs for leak-detection assertions."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    @property
    def active(self) -> set[str]:
        return set(self.created) - set(self.deleted)


# ---------------------------------------------------------------------------
# MCP call counters
# ---------------------------------------------------------------------------


class McpCallRecorder:
    """Records MCP calls for assertion in tests."""

    def __init__(self) -> None:
        self.swarm_init_calls: list[dict] = []
        self.agent_spawn_calls: list[dict] = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracker() -> WorktreeTracker:
    return WorktreeTracker()


@pytest.fixture
def recorder() -> McpCallRecorder:
    return McpCallRecorder()


@pytest.fixture
def mock_workspace(tracker: WorktreeTracker) -> WorkspaceClient:
    """WorkspaceClient with mocked HTTP; records creates/deletes in tracker."""
    client = MagicMock(spec=WorkspaceClient)
    _worktree_counter = [0]

    async def _create(task_id: str, repo: str, base_ref: str = "main", topic: str = "ops"):
        tracker.created.append(task_id)
        path = f"/code/mnemonic-workspaces/{task_id}"
        return WorktreeCreated(
            task_id=task_id,
            path=path,
            env_path=f"{path}/.env",
            created_at="2026-05-12T00:00:00Z",
        )

    async def _delete(task_id: str):
        tracker.deleted.append(task_id)
        return WorktreeDeleted(task_id=task_id, deleted=True)

    client.create_worktree = AsyncMock(side_effect=_create)
    client.delete_worktree = AsyncMock(side_effect=_delete)
    return client


@pytest.fixture
def mock_ruflo(recorder: McpCallRecorder) -> RufloClient:
    """RufloClient with mocked MCP callables; records calls in recorder."""
    _swarm_counter = [0]
    _agent_counter = [0]

    async def swarm_init_fn(**kwargs):
        recorder.swarm_init_calls.append(kwargs)
        _swarm_counter[0] += 1
        return {"swarm_id": f"swarm-{_swarm_counter[0]}"}

    async def agent_spawn_fn(**kwargs):
        recorder.agent_spawn_calls.append(kwargs)
        _agent_counter[0] += 1
        return {"agent_id": f"agent-{_agent_counter[0]}"}

    return RufloClient(swarm_init_fn=swarm_init_fn, agent_spawn_fn=agent_spawn_fn)


@pytest.fixture
def bridge(mock_workspace: WorkspaceClient, mock_ruflo: RufloClient) -> SwarmBridge:
    """SwarmBridge wired to mock workspace + ruflo clients, no capacity wait."""
    return SwarmBridge(
        workspace=mock_workspace,
        ruflo=mock_ruflo,
        capacity_wait_s=0.0,
    )


@pytest.fixture
def simple_plan() -> FeaturePlan:
    """A feature plan with two tasks in a single wave."""
    return FeaturePlan(
        feature_id="feat-smoke",
        waves=[
            [
                TaskSpec(task_id="TASK-1", brief="implement foo", repo="mnemonic-core"),
                TaskSpec(task_id="TASK-2", brief="implement bar", repo="mnemonic-core"),
            ]
        ],
    )


@pytest.fixture
def multi_wave_plan() -> FeaturePlan:
    """A feature plan with two waves."""
    return FeaturePlan(
        feature_id="feat-waves",
        waves=[
            [TaskSpec(task_id="WAVE1-T1", brief="wave 1 task 1", repo="mnemonic-core")],
            [
                TaskSpec(task_id="WAVE2-T1", brief="wave 2 task 1", repo="mnemonic-core"),
                TaskSpec(task_id="WAVE2-T2", brief="wave 2 task 2", repo="mnemonic-core"),
            ],
        ],
    )
