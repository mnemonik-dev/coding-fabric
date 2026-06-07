"""Cleanup-gc sub-loop tests.

Contract:
  * Terminal jobs with ``cleanup_at <= now``: rmtree(worktree_path), append a
    serialized record to ``queue.archive.jsonl``, then drop them from
    ``queue.jsonl``.
  * Non-terminal jobs are NEVER collected, even if ``cleanup_at`` is past.
  * Missing worktree (``FileNotFoundError``) is treated as success — the job is
    still archived.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_publisher import cleanup, queue
from content_publisher.models import JobStatus

TERMINAL_STATUSES = (
    JobStatus.DONE,
    JobStatus.REJECTED,
    JobStatus.REGENERATED,
    JobStatus.PUBLISH_FAILED,
    JobStatus.ATTEST_FAILED,
    JobStatus.FAILED,
)


def _seed_queue(path: Path, jobs) -> None:
    payload = "\n".join(j.model_dump_json() for j in jobs) + "\n"
    path.write_text(payload, encoding="utf-8")


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
def test_terminal_jobs_with_cleanup_at_past_are_gced(
    tmp_queue_path: Path, tmp_path: Path, make_job, status
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "article.md").write_text("# a\n", encoding="utf-8")

    now = datetime.now(UTC)
    job = make_job(
        status=status,
        worktree_path=str(worktree),
        cleanup_at=now - timedelta(seconds=1),
    )
    _seed_queue(tmp_queue_path, [job])

    cleanup.cleanup_gc_once(tmp_queue_path)

    # worktree gone, queue.jsonl empty, archive contains the job.
    assert not worktree.exists()
    remaining = queue.load(tmp_queue_path)
    assert remaining == []
    archive = tmp_queue_path.with_name("queue.archive.jsonl")
    assert archive.exists()
    archived = [json.loads(ln) for ln in archive.read_text().splitlines() if ln.strip()]
    assert len(archived) == 1
    assert archived[0]["id"] == job.id
    assert archived[0]["status"] == status.value


def test_terminal_job_with_future_cleanup_at_is_not_gced(
    tmp_queue_path: Path, tmp_path: Path, make_job
) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    job = make_job(
        status=JobStatus.DONE,
        worktree_path=str(worktree),
        cleanup_at=datetime.now(UTC) + timedelta(hours=1),
    )
    _seed_queue(tmp_queue_path, [job])

    cleanup.cleanup_gc_once(tmp_queue_path)

    assert worktree.exists()
    assert len(queue.load(tmp_queue_path)) == 1


def test_non_terminal_jobs_are_not_gced_even_if_cleanup_at_set(
    tmp_queue_path: Path, tmp_path: Path, make_job
) -> None:
    """A non-terminal job with cleanup_at in the past is left intact.

    (Defensive: cleanup_at should only ever be set on preview-sent and later
    states per the worker, but the gc must not act on intent — only on terminal
    status.)
    """
    worktree = tmp_path / "wt"
    worktree.mkdir()

    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        worktree_path=str(worktree),
        cleanup_at=datetime.now(UTC) - timedelta(hours=1),
        approval_deadline=datetime.now(UTC) - timedelta(minutes=1),
        preview_pending=False,
    )
    _seed_queue(tmp_queue_path, [job])

    cleanup.cleanup_gc_once(tmp_queue_path)

    assert worktree.exists()
    assert len(queue.load(tmp_queue_path)) == 1


def test_cleanup_gc_missing_worktree_still_archives(
    tmp_queue_path: Path, tmp_path: Path, make_job
) -> None:
    """Worktree already removed by operator — still archive the job."""
    job = make_job(
        status=JobStatus.DONE,
        worktree_path=str(tmp_path / "vanished"),
        cleanup_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    _seed_queue(tmp_queue_path, [job])

    cleanup.cleanup_gc_once(tmp_queue_path)

    assert queue.load(tmp_queue_path) == []
    archive = tmp_queue_path.with_name("queue.archive.jsonl")
    assert archive.exists()
    assert job.id in archive.read_text()


def test_cleanup_gc_no_worktree_path_set(tmp_queue_path: Path, make_job) -> None:
    """Terminal job that never got a worktree (e.g. early failed): still archive."""
    job = make_job(
        status=JobStatus.FAILED,
        worktree_path=None,
        cleanup_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    _seed_queue(tmp_queue_path, [job])

    cleanup.cleanup_gc_once(tmp_queue_path)

    assert queue.load(tmp_queue_path) == []
    archive = tmp_queue_path.with_name("queue.archive.jsonl")
    assert job.id in archive.read_text()
