"""Scoring gate behaviour: high score passes, low score truncates to 3 issues."""

from __future__ import annotations

import asyncio
import json
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
        prompt="topic",
        mode="approval",
        chat_id=1,
        thread_id=2,
        reply_to_message_id=3,
    )


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    score_value: int,
    issues: list[str],
    worktree_root: Path,
    article_text: str = "# Title\n\nBody text.\n",
    preview_segments: list[str] | None = None,
) -> None:
    """Wire fake subprocess + fake renderer so the worker reaches preview-sent."""
    monkeypatch.setattr(worker, "WORKTREE_ROOT", worktree_root)
    monkeypatch.setattr(worker, "APPROVAL_TIMEOUT_MIN", 5)

    call_count = {"n": 0}

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProc:
        call_count["n"] += 1
        if call_count["n"] == 1:
            cwd = Path(kwargs.get("cwd", "."))
            (cwd / "article.md").write_text(article_text)
            return _FakeProc(returncode=0)
        payload = json.dumps({"score": score_value, "issues": issues}).encode()
        return _FakeProc(returncode=0, stdout=payload)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # Bypass real mnemonik_blogger import in render.py — the local snapshot
    # may not ship the verified seam.
    def fake_render_preview(article_path: Path) -> list[str]:
        return preview_segments if preview_segments is not None else ["preview segment"]

    monkeypatch.setattr(worker, "render_preview", fake_render_preview)


async def test_high_score_no_retry_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_queue_path: Path,
) -> None:
    """score >= min -> job lands in preview-sent with issues = []."""
    _stub_env(monkeypatch)
    monkeypatch.setattr(worker, "QUEUE_PATH", tmp_queue_path)
    worktree_root = tmp_path / "work"
    worktree_root.mkdir()
    _patch_pipeline(
        monkeypatch,
        score_value=92,
        issues=[],
        worktree_root=worktree_root,
        preview_segments=["seg-one"],
    )

    job = _seed_queued_job(tmp_queue_path)
    await worker.run_writing_to_preview(job)

    refreshed = next(j for j in queue.load(tmp_queue_path) if j.id == job.id)
    assert refreshed.status == JobStatus.PREVIEW_SENT
    assert refreshed.score == 92
    assert refreshed.issues == []
    assert refreshed.preview_pending is True
    assert refreshed.preview_segments == ["seg-one"]
    assert refreshed.approval_deadline is not None
    assert refreshed.cleanup_at is not None


async def test_low_score_truncates_issues_to_three(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_queue_path: Path,
) -> None:
    """analyze returned 7 issues -> worker stores exactly the first 3, order preserved."""
    _stub_env(monkeypatch)
    monkeypatch.setattr(worker, "QUEUE_PATH", tmp_queue_path)
    worktree_root = tmp_path / "work"
    worktree_root.mkdir()
    raw_issues = [f"issue-{i}" for i in range(7)]
    _patch_pipeline(
        monkeypatch,
        score_value=55,
        issues=raw_issues,
        worktree_root=worktree_root,
    )

    job = _seed_queued_job(tmp_queue_path)
    await worker.run_writing_to_preview(job)

    refreshed = next(j for j in queue.load(tmp_queue_path) if j.id == job.id)
    assert refreshed.status == JobStatus.PREVIEW_SENT
    assert refreshed.score == 55
    assert refreshed.issues == ["issue-0", "issue-1", "issue-2"]
