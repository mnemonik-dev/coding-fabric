"""
Tests for fabric.integrations.swarm_bridge.

TDD anchors from tasks/14.md:
    test_swarm_init_called_once_per_feature
    test_agent_spawn_receives_distinct_cwd
    test_409_capacity_surfaced
    test_worktree_cleaned_on_swarm_completion
    test_partial_failure_does_not_leak_worktrees
    test_concurrent_features_dont_collide  (added by task instructions)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from fabric.integrations.workspace_client import (
    CapacityError,
    WorkspaceClient,
    WorktreeCreated,
    WorktreeDeleted,
    WorkspaceClientError,
)
from fabric.integrations.ruflo_client import AgentConfig, RufloClient, SwarmConfig, SwarmHandle
from fabric.integrations.swarm_bridge import (
    DispatchResult,
    FeaturePlan,
    SwarmBridge,
    TaskSpec,
)
from .conftest import McpCallRecorder, WorktreeTracker


# ===========================================================================
# test_swarm_init_called_once_per_feature
# ===========================================================================


@pytest.mark.asyncio
async def test_swarm_init_called_once_per_feature(
    bridge: SwarmBridge,
    recorder: McpCallRecorder,
    simple_plan: FeaturePlan,
) -> None:
    """swarm_init must be called exactly once regardless of task count."""
    result = await bridge.dispatch(simple_plan)

    assert len(recorder.swarm_init_calls) == 1
    init_call = recorder.swarm_init_calls[0]
    assert init_call["feature_id"] == simple_plan.feature_id
    # task_count matches total tasks across all waves
    assert init_call["task_count"] == 2
    assert isinstance(result.swarm.swarm_id, str)
    assert result.swarm.swarm_id != ""


@pytest.mark.asyncio
async def test_swarm_init_called_once_for_multi_wave_plan(
    bridge: SwarmBridge,
    recorder: McpCallRecorder,
    multi_wave_plan: FeaturePlan,
) -> None:
    """swarm_init is still called once for multi-wave plans (3 tasks total)."""
    await bridge.dispatch(multi_wave_plan)

    assert len(recorder.swarm_init_calls) == 1
    assert recorder.swarm_init_calls[0]["task_count"] == 3


# ===========================================================================
# test_agent_spawn_receives_distinct_cwd
# ===========================================================================


@pytest.mark.asyncio
async def test_agent_spawn_receives_distinct_cwd(
    bridge: SwarmBridge,
    recorder: McpCallRecorder,
    simple_plan: FeaturePlan,
) -> None:
    """Each agent_spawn call must receive a distinct cwd path."""
    await bridge.dispatch(simple_plan)

    assert len(recorder.agent_spawn_calls) == 2
    cwds = [c["cwd"] for c in recorder.agent_spawn_calls]
    # All cwd values are non-empty strings
    for cwd in cwds:
        assert isinstance(cwd, str) and cwd
    # All distinct
    assert len(set(cwds)) == len(cwds), f"duplicate cwd found: {cwds}"


@pytest.mark.asyncio
async def test_agent_spawn_cwd_contains_task_id(
    bridge: SwarmBridge,
    recorder: McpCallRecorder,
    simple_plan: FeaturePlan,
) -> None:
    """Each agent's cwd path should contain its task_id (workspace-manager convention)."""
    await bridge.dispatch(simple_plan)

    for call_kwargs in recorder.agent_spawn_calls:
        assert call_kwargs["task_id"] in call_kwargs["cwd"]


@pytest.mark.asyncio
async def test_agent_spawn_receives_correct_swarm_id(
    bridge: SwarmBridge,
    recorder: McpCallRecorder,
    simple_plan: FeaturePlan,
) -> None:
    """All agent_spawn calls reference the same swarm_id as swarm_init returned."""
    result = await bridge.dispatch(simple_plan)
    swarm_id = result.swarm.swarm_id

    for call_kwargs in recorder.agent_spawn_calls:
        assert call_kwargs["swarm_id"] == swarm_id


# ===========================================================================
# test_409_capacity_surfaced
# ===========================================================================


@pytest.mark.asyncio
async def test_409_capacity_surfaced(
    mock_ruflo: RufloClient,
    recorder: McpCallRecorder,
) -> None:
    """A full worktree pool (HTTP 409) is surfaced as CapacityError to the caller."""
    workspace = MagicMock(spec=WorkspaceClient)
    workspace.create_worktree = AsyncMock(
        side_effect=CapacityError("TASK-1", detail="pool full")
    )
    workspace.delete_worktree = AsyncMock(
        return_value=WorktreeDeleted(task_id="TASK-1", deleted=True)
    )

    bridge = SwarmBridge(workspace=workspace, ruflo=mock_ruflo, capacity_wait_s=0.0)
    plan = FeaturePlan(
        feature_id="feat-capacity",
        waves=[[TaskSpec(task_id="TASK-1", brief="x", repo="mnemonic-core")]],
    )

    with pytest.raises(CapacityError):
        await bridge.dispatch(plan)


@pytest.mark.asyncio
async def test_409_retried_once_before_surfacing(
    mock_ruflo: RufloClient,
) -> None:
    """On 409 the bridge waits then retries exactly once before raising."""
    call_count = [0]

    async def capacity_create(**kwargs):
        call_count[0] += 1
        raise CapacityError(kwargs["task_id"], "pool full")

    workspace = MagicMock(spec=WorkspaceClient)
    workspace.create_worktree = AsyncMock(side_effect=capacity_create)
    workspace.delete_worktree = AsyncMock(
        return_value=WorktreeDeleted(task_id="TASK-1", deleted=True)
    )

    bridge = SwarmBridge(workspace=workspace, ruflo=mock_ruflo, capacity_wait_s=0.0)
    plan = FeaturePlan(
        feature_id="feat-retry",
        waves=[[TaskSpec(task_id="TASK-1", brief="x", repo="mnemonic-core")]],
    )

    with pytest.raises(CapacityError):
        await bridge.dispatch(plan)

    # create_worktree called twice: first attempt + one retry
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_409_resolves_on_retry(
    mock_ruflo: RufloClient,
    recorder: McpCallRecorder,
) -> None:
    """If retry succeeds after 409 the feature proceeds normally."""
    call_count = [0]

    async def flaky_create(task_id: str, repo: str, base_ref: str = "main", topic: str = "ops"):
        call_count[0] += 1
        if call_count[0] == 1:
            raise CapacityError(task_id, "pool full")
        return WorktreeCreated(
            task_id=task_id,
            path=f"/code/mnemonic-workspaces/{task_id}",
            env_path=f"/code/mnemonic-workspaces/{task_id}/.env",
            created_at="2026-05-12T00:00:00Z",
        )

    workspace = MagicMock(spec=WorkspaceClient)
    workspace.create_worktree = AsyncMock(side_effect=flaky_create)
    workspace.delete_worktree = AsyncMock(
        return_value=WorktreeDeleted(task_id="TASK-1", deleted=True)
    )

    bridge = SwarmBridge(workspace=workspace, ruflo=mock_ruflo, capacity_wait_s=0.0)
    plan = FeaturePlan(
        feature_id="feat-flaky",
        waves=[[TaskSpec(task_id="TASK-1", brief="x", repo="mnemonic-core")]],
    )

    result = await bridge.dispatch(plan)
    assert len(result.agents) == 1


# ===========================================================================
# test_worktree_cleaned_on_swarm_completion
# ===========================================================================


@pytest.mark.asyncio
async def test_worktree_cleaned_on_swarm_completion(
    bridge: SwarmBridge,
    tracker: WorktreeTracker,
    simple_plan: FeaturePlan,
) -> None:
    """After successful dispatch all created worktrees must be deleted."""
    await bridge.dispatch(simple_plan)

    assert set(tracker.created) == {"TASK-1", "TASK-2"}
    assert set(tracker.deleted) == {"TASK-1", "TASK-2"}
    assert tracker.active == set(), f"leaks: {tracker.active}"


@pytest.mark.asyncio
async def test_worktree_cleaned_for_multi_wave(
    bridge: SwarmBridge,
    tracker: WorktreeTracker,
    multi_wave_plan: FeaturePlan,
) -> None:
    """Worktrees for all waves are cleaned up on completion."""
    await bridge.dispatch(multi_wave_plan)

    expected_ids = {"WAVE1-T1", "WAVE2-T1", "WAVE2-T2"}
    assert set(tracker.created) == expected_ids
    assert tracker.active == set(), f"leaks: {tracker.active}"


# ===========================================================================
# test_partial_failure_does_not_leak_worktrees
# ===========================================================================


@pytest.mark.asyncio
async def test_partial_failure_does_not_leak_worktrees(
    mock_workspace: WorkspaceClient,
    tracker: WorktreeTracker,
) -> None:
    """When one agent_spawn fails, worktrees for all tasks in the wave are deleted."""
    spawn_count = [0]

    async def flaky_spawn(**kwargs):
        spawn_count[0] += 1
        if kwargs["task_id"] == "TASK-2":
            raise RuntimeError("agent_spawn failed deliberately")
        return {"agent_id": f"agent-{spawn_count[0]}"}

    ruflo = RufloClient(
        swarm_init_fn=AsyncMock(return_value={"swarm_id": "swarm-x"}),
        agent_spawn_fn=flaky_spawn,
    )
    bridge = SwarmBridge(workspace=mock_workspace, ruflo=ruflo, capacity_wait_s=0.0)

    plan = FeaturePlan(
        feature_id="feat-partial-fail",
        waves=[
            [
                TaskSpec(task_id="TASK-1", brief="ok task", repo="mnemonic-core"),
                TaskSpec(task_id="TASK-2", brief="failing task", repo="mnemonic-core"),
            ]
        ],
    )

    with pytest.raises(RuntimeError, match="agent_spawn failed deliberately"):
        await bridge.dispatch(plan)

    # Both worktrees that were created must have been deleted
    assert tracker.active == set(), (
        f"Leaked worktrees after partial failure: {tracker.active}\n"
        f"created={tracker.created}, deleted={tracker.deleted}"
    )


@pytest.mark.asyncio
async def test_partial_failure_all_creates_before_spawn(
    tracker: WorktreeTracker,
) -> None:
    """Each task independently creates+deletes its worktree; single task failure
    does not prevent others from being cleaned up."""
    # Simulate: TASK-2 create succeeds but spawn fails; TASK-1 create+spawn succeeds
    create_count = [0]
    delete_records: list[str] = []

    async def track_create(task_id: str, repo: str, base_ref: str = "main", topic: str = "ops"):
        create_count[0] += 1
        tracker.created.append(task_id)
        return WorktreeCreated(
            task_id=task_id,
            path=f"/code/mnemonic-workspaces/{task_id}",
            env_path=f"/code/mnemonic-workspaces/{task_id}/.env",
            created_at="2026-05-12T00:00:00Z",
        )

    async def track_delete(task_id: str):
        tracker.deleted.append(task_id)
        return WorktreeDeleted(task_id=task_id, deleted=True)

    workspace = MagicMock(spec=WorkspaceClient)
    workspace.create_worktree = AsyncMock(side_effect=track_create)
    workspace.delete_worktree = AsyncMock(side_effect=track_delete)

    spawn_count = [0]

    async def flaky_spawn(**kwargs):
        spawn_count[0] += 1
        if kwargs["task_id"] == "TASK-B":
            raise RuntimeError("spawn boom")
        return {"agent_id": f"agent-ok-{spawn_count[0]}"}

    ruflo = RufloClient(
        swarm_init_fn=AsyncMock(return_value={"swarm_id": "swarm-partial"}),
        agent_spawn_fn=flaky_spawn,
    )
    bridge = SwarmBridge(workspace=workspace, ruflo=ruflo, capacity_wait_s=0.0)

    plan = FeaturePlan(
        feature_id="feat-partial",
        waves=[
            [
                TaskSpec(task_id="TASK-A", brief="good", repo="mnemonic-core"),
                TaskSpec(task_id="TASK-B", brief="bad", repo="mnemonic-core"),
            ]
        ],
    )

    with pytest.raises(RuntimeError, match="spawn boom"):
        await bridge.dispatch(plan)

    # No active leaks
    assert tracker.active == set(), f"leaked: {tracker.active}"


# ===========================================================================
# test_concurrent_features_dont_collide
# ===========================================================================


@pytest.mark.asyncio
async def test_concurrent_features_dont_collide() -> None:
    """Two features dispatched concurrently must receive distinct cwd values."""
    cwd_registry: list[str] = []
    lock = asyncio.Lock()

    async def swarm_init_fn(**kwargs):
        return {"swarm_id": f"swarm-{kwargs['feature_id']}"}

    async def agent_spawn_fn(**kwargs):
        async with lock:
            cwd_registry.append(kwargs["cwd"])
        return {"agent_id": f"agent-{kwargs['task_id']}"}

    def make_workspace(feature_label: str) -> WorkspaceClient:
        workspace = MagicMock(spec=WorkspaceClient)
        counter = [0]

        async def create(task_id: str, repo: str, base_ref: str = "main", topic: str = "ops"):
            counter[0] += 1
            # Include both feature and task label so paths are globally distinct
            path = f"/code/mnemonic-workspaces/{feature_label}-{task_id}"
            return WorktreeCreated(
                task_id=task_id,
                path=path,
                env_path=f"{path}/.env",
                created_at="2026-05-12T00:00:00Z",
            )

        async def delete(task_id: str):
            return WorktreeDeleted(task_id=task_id, deleted=True)

        workspace.create_worktree = AsyncMock(side_effect=create)
        workspace.delete_worktree = AsyncMock(side_effect=delete)
        return workspace

    ruflo_a = RufloClient(swarm_init_fn=agent_spawn_fn, agent_spawn_fn=agent_spawn_fn)
    ruflo_b = RufloClient(swarm_init_fn=agent_spawn_fn, agent_spawn_fn=agent_spawn_fn)

    # Build separate bridge+ruflo per feature
    async def swarm_init_fn_a(**kwargs):
        return {"swarm_id": f"swarm-feat-alpha"}

    async def swarm_init_fn_b(**kwargs):
        return {"swarm_id": f"swarm-feat-beta"}

    ruflo_a = RufloClient(swarm_init_fn=swarm_init_fn_a, agent_spawn_fn=agent_spawn_fn)
    ruflo_b = RufloClient(swarm_init_fn=swarm_init_fn_b, agent_spawn_fn=agent_spawn_fn)

    bridge_a = SwarmBridge(workspace=make_workspace("alpha"), ruflo=ruflo_a, capacity_wait_s=0.0)
    bridge_b = SwarmBridge(workspace=make_workspace("beta"), ruflo=ruflo_b, capacity_wait_s=0.0)

    plan_a = FeaturePlan(
        feature_id="feat-alpha",
        waves=[
            [
                TaskSpec(task_id="ALPHA-1", brief="alpha task 1", repo="mnemonic-core"),
                TaskSpec(task_id="ALPHA-2", brief="alpha task 2", repo="mnemonic-core"),
            ]
        ],
    )
    plan_b = FeaturePlan(
        feature_id="feat-beta",
        waves=[
            [
                TaskSpec(task_id="BETA-1", brief="beta task 1", repo="mnemonic-mcp"),
                TaskSpec(task_id="BETA-2", brief="beta task 2", repo="mnemonic-mcp"),
            ]
        ],
    )

    results = await asyncio.gather(bridge_a.dispatch(plan_a), bridge_b.dispatch(plan_b))
    result_a, result_b = results

    # Distinct swarm IDs
    assert result_a.swarm.swarm_id != result_b.swarm.swarm_id

    # All four cwd values must be distinct
    assert len(cwd_registry) == 4
    assert len(set(cwd_registry)) == 4, f"cwd collision detected: {cwd_registry}"


# ===========================================================================
# Additional edge cases
# ===========================================================================


@pytest.mark.asyncio
async def test_dispatch_result_contains_all_agents(
    bridge: SwarmBridge,
    recorder: McpCallRecorder,
    multi_wave_plan: FeaturePlan,
) -> None:
    """DispatchResult.agents has one handle per task."""
    result = await bridge.dispatch(multi_wave_plan)

    assert len(result.agents) == 3  # 1 + 2 across two waves


@pytest.mark.asyncio
async def test_waves_processed_sequentially(
    mock_workspace: WorkspaceClient,
) -> None:
    """Tasks in wave N must not spawn before all tasks in wave N-1 have spawned."""
    event_log: list[str] = []

    async def swarm_init_fn(**kwargs):
        return {"swarm_id": "swarm-seq"}

    async def agent_spawn_fn(**kwargs):
        event_log.append(("spawn", kwargs["task_id"]))
        return {"agent_id": f"a-{kwargs['task_id']}"}

    ruflo = RufloClient(swarm_init_fn=swarm_init_fn, agent_spawn_fn=agent_spawn_fn)
    bridge = SwarmBridge(workspace=mock_workspace, ruflo=ruflo, capacity_wait_s=0.0)

    plan = FeaturePlan(
        feature_id="feat-seq",
        waves=[
            [TaskSpec(task_id="W1-T1", brief="w1t1", repo="mnemonic-core")],
            [TaskSpec(task_id="W2-T1", brief="w2t1", repo="mnemonic-core")],
        ],
    )
    await bridge.dispatch(plan)

    spawned_ids = [e[1] for e in event_log]
    w1_idx = spawned_ids.index("W1-T1")
    w2_idx = spawned_ids.index("W2-T1")
    assert w1_idx < w2_idx, "Wave 2 task spawned before Wave 1 task"
