"""Crash recovery: kill the worker at each in-flight state, verify recovery
behaviour without losing the forensic tentative_publish_started_at hint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from content_publisher import queue, recovery
from content_publisher.models import JobStatus


class _PR:
    def __init__(self, **kw):
        self.ok = kw.get("ok", True)
        self.primary_url = kw.get("primary_url", "https://t.me/c/1/2")
        self.urls = kw.get("urls", [])
        self.ids = kw.get("ids", [2])
        self.error = kw.get("error", None)


class _CR:
    def __init__(self, results):
        self.results = results


def test_kill_at_writing_restarts_from_queued(
    tmp_queue_path: Path, make_job
) -> None:
    job = make_job(status=JobStatus.WRITING, worktree_path="/tmp/wt")
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.QUEUED


def test_kill_at_scoring_restarts_from_queued(
    tmp_queue_path: Path, make_job
) -> None:
    job = make_job(status=JobStatus.SCORING, worktree_path="/tmp/wt")
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.QUEUED


def test_kill_at_preview_sent_left_alone(tmp_queue_path: Path, make_job) -> None:
    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        approval_deadline=datetime.now(UTC),
        preview_pending=False,
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.PREVIEW_SENT


@pytest.mark.asyncio
async def test_kill_at_publishing_just_retries(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """A job stuck in publishing is left in publishing — normal publish path
    picks it back up. tentative_publish_started_at survives.
    """
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 1_000_000)

    article = tmp_path / "article.md"
    article.write_text("body\n", encoding="utf-8")

    seeded_tentative = datetime.now(UTC)
    job = make_job(
        status=JobStatus.PUBLISHING,
        article_path=str(article),
        tentative_publish_started_at=seeded_tentative,
        score=90,
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    recovery.recover_in_flight(tmp_queue_path)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHING
    assert refreshed.tentative_publish_started_at == seeded_tentative

    # Now the publish step picks it back up — exactly one in-process call.
    call_count = {"n": 0}

    def fake_call(*args, **kwargs):  # noqa: ANN002, ANN003
        call_count["n"] += 1
        return _CR([_PR(ok=True, primary_url="https://t.me/c/1/2", ids=[2])])

    with patch.object(publish, "_run_campaign_from_article", side_effect=fake_call):
        await publish.publish_step(job_id=job.id)

    assert call_count["n"] == 1
    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHED


def test_kill_at_attest_pending_left_alone(tmp_queue_path: Path, make_job) -> None:
    job = make_job(
        status=JobStatus.ATTEST_PENDING,
        attest_pending_since=datetime.now(UTC),
        post_url="https://t.me/c/1/2",
        content_sha256="a" * 64,
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.ATTEST_PENDING
