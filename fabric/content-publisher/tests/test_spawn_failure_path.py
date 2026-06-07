"""Worker failure-path coverage.

When either ``claude`` or ``analyze_blog.py`` exits non-zero, the worker MUST
move the job to ``failed`` + set ``notify_pending=true`` + persist a
diagnostic ``error_message``. The worktree must remain on disk so the
cleanup-gc loop (Task 6) can reclaim it on ``cleanup_at``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from content_publisher import queue, worker
from content_publisher.models import Job, JobStatus


class _FakeProc:
    def __init__(
        self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/var/lib/content-publisher")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
    monkeypatch.setenv("MNEMONIK_CLAUDE_BLOG_PATH", "/opt/claude-blog")


def _seed_queued_job(queue_path: Path) -> Job:
    return queue.append_job(
        queue_path,
        prompt="explain mnemonik",
        mode="approval",
        chat_id=1,
        thread_id=2,
        reply_to_message_id=3,
    )


async def test_claude_nonzero_exit_marks_failed_and_notify_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_queue_path: Path,
) -> None:
    """claude exits 1 -> job goes to FAILED, notify_pending=True, worktree kept."""
    _stub_env(monkeypatch)
    worktree_root = tmp_path / "work"
    worktree_root.mkdir()
    monkeypatch.setattr(worker, "WORKTREE_ROOT", worktree_root)
    monkeypatch.setattr(worker, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(worker, "APPROVAL_TIMEOUT_MIN", 5)

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(returncode=1, stderr=b"claude: oauth token expired\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = _seed_queued_job(tmp_queue_path)

    await worker.run_writing_to_preview(job)

    refreshed = next(j for j in queue.load(tmp_queue_path) if j.id == job.id)
    assert refreshed.status == JobStatus.FAILED
    assert refreshed.notify_pending is True
    assert refreshed.error_message
    assert "oauth token expired" in refreshed.error_message
    # Worktree must still exist for cleanup-gc to find on cleanup_at.
    expected_wt = worktree_root / job.id
    assert expected_wt.exists()


async def test_analyze_blog_nonzero_exit_marks_failed_and_notify_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_queue_path: Path,
) -> None:
    """analyze_blog exits 1 after claude succeeds -> same FAILED + notify."""
    _stub_env(monkeypatch)
    worktree_root = tmp_path / "work"
    worktree_root.mkdir()
    monkeypatch.setattr(worker, "WORKTREE_ROOT", worktree_root)
    monkeypatch.setattr(worker, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(worker, "APPROVAL_TIMEOUT_MIN", 5)

    call_count = {"n": 0}

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # claude — write article.md and exit 0
            cwd = Path(kwargs.get("cwd", "."))
            (cwd / "article.md").write_text("# stub article\n")
            return _FakeProc(returncode=0)
        # analyze_blog — exit 1
        return _FakeProc(returncode=1, stderr=b"analyze_blog: model timeout\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = _seed_queued_job(tmp_queue_path)

    await worker.run_writing_to_preview(job)

    refreshed = next(j for j in queue.load(tmp_queue_path) if j.id == job.id)
    assert refreshed.status == JobStatus.FAILED
    assert refreshed.notify_pending is True
    assert refreshed.error_message
    assert "model timeout" in refreshed.error_message
    expected_wt = worktree_root / job.id
    assert expected_wt.exists()


async def test_missing_article_after_claude_success_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_queue_path: Path,
) -> None:
    """claude returns 0 but never produced article.md -> failed."""
    _stub_env(monkeypatch)
    worktree_root = tmp_path / "work"
    worktree_root.mkdir()
    monkeypatch.setattr(worker, "WORKTREE_ROOT", worktree_root)
    monkeypatch.setattr(worker, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(worker, "APPROVAL_TIMEOUT_MIN", 5)

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        # claude exits clean but writes nothing
        return _FakeProc(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    job = _seed_queued_job(tmp_queue_path)
    await worker.run_writing_to_preview(job)

    refreshed = next(j for j in queue.load(tmp_queue_path) if j.id == job.id)
    assert refreshed.status == JobStatus.FAILED
    assert refreshed.notify_pending is True
    assert refreshed.error_message
    assert "article.md" in refreshed.error_message
