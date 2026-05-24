"""Unit tests for auto_merge.

KaneoClient is mocked — real HTTP would need a live Kaneo. The tick
logic + parsing helpers stand on their own.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from auto_merge import (
    MergePolicy,
    _parse_auto_merge_deadline,
    _read_deadline_from_comments,
    schedule_or_tick,
)
from kaneo_client import Ticket


def _ticket(*, status: str = "ready-to-merge", labels: tuple[str, ...] = (), description: str = ""):
    return Ticket(
        id="TASK-1",
        title="t",
        description=description,
        status=status,
        project_id="proj-1",
        labels=labels,
        raw={"id": "TASK-1"},
    )


def test_parse_deadline_label() -> None:
    deadline = _parse_auto_merge_deadline(["auto-merge:2026-05-25T12:00:00Z"])
    assert deadline is not None
    assert deadline == datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_deadline_label_bad_format() -> None:
    assert _parse_auto_merge_deadline(["auto-merge:nonsense"]) is None
    assert _parse_auto_merge_deadline(["other-label"]) is None


@pytest.mark.asyncio
async def test_read_deadline_from_comments_picks_latest() -> None:
    kaneo = AsyncMock()
    kaneo.list_comments.return_value = [
        {"body": "symphony: ready to auto-merge. Window closes at 2026-05-24T10:00:00Z. Reply ..."},
        {"body": "other comment"},
        {"body": "symphony: ready to auto-merge. Window closes at 2026-05-25T10:00:00Z. Reply ..."},
    ]
    got = await _read_deadline_from_comments(kaneo, "TASK-1")
    assert got is not None
    assert got == datetime(2026, 5, 25, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_always_human_merge_short_circuits() -> None:
    kaneo = AsyncMock()
    policy = MergePolicy(merge_policy="always-human-merge")
    out = await schedule_or_tick(_ticket(), kaneo=kaneo, policy=policy, repo_root=Path("."))
    assert out == "skip: always-human-merge"
    kaneo.create_comment.assert_not_called()
    kaneo.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_reject_label_cancels() -> None:
    kaneo = AsyncMock()
    kaneo.list_comments.return_value = []
    policy = MergePolicy(merge_policy="auto-with-veto")
    out = await schedule_or_tick(
        _ticket(labels=("rejected",)),
        kaneo=kaneo,
        policy=policy,
        repo_root=Path("."),
    )
    assert out == "cancelled-veto"
    kaneo.update_task_status.assert_awaited_once_with("TASK-1", "review")


@pytest.mark.asyncio
async def test_first_tick_schedules() -> None:
    kaneo = AsyncMock()
    kaneo.list_comments.return_value = []
    policy = MergePolicy(merge_policy="auto-with-veto", veto_window_hours=24)
    out = await schedule_or_tick(_ticket(), kaneo=kaneo, policy=policy, repo_root=Path("."))
    assert out.startswith("scheduled:")
    # Comment posted explaining the veto window
    kaneo.create_comment.assert_awaited()
    body = kaneo.create_comment.await_args.args[1]
    assert "ready to auto-merge" in body
    assert "/reject TASK-1" in body


@pytest.mark.asyncio
async def test_waiting_when_deadline_in_future() -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kaneo = AsyncMock()
    kaneo.list_comments.return_value = [
        {"body": f"symphony: ready to auto-merge. Window closes at {future}. Reply ..."},
    ]
    policy = MergePolicy(merge_policy="auto-with-veto")
    out = await schedule_or_tick(_ticket(), kaneo=kaneo, policy=policy, repo_root=Path("."))
    assert out.startswith("waiting:")
    kaneo.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_no_pr_link_skips_merge() -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kaneo = AsyncMock()
    kaneo.list_comments.return_value = [
        {"body": f"symphony: ready to auto-merge. Window closes at {past}. Reply ..."},
    ]
    policy = MergePolicy(merge_policy="auto-with-veto")
    out = await schedule_or_tick(
        _ticket(description="No PR here"),
        kaneo=kaneo,
        policy=policy,
        repo_root=Path("."),
    )
    assert out == "skip: no PR link"
    # Posts an explanatory comment but does not transition status
    kaneo.update_task_status.assert_not_called()
