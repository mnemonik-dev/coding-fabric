"""Deadline-checker sub-loop tests.

Two independent passes per tick:
  1) ``preview-sent`` with ``approval_deadline <= now`` → CAS to ``publishing``.
  2) ``attest-pending`` → re-attempt attestation via ``attest.attest_once``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from content_publisher import deadline, queue
from content_publisher.models import JobStatus

pytestmark = pytest.mark.asyncio


def _seed(path: Path, jobs) -> None:
    path.write_text(
        "\n".join(j.model_dump_json() for j in jobs) + "\n", encoding="utf-8"
    )


async def test_preview_sent_past_deadline_transitions_to_publishing(
    tmp_queue_path: Path, make_job
) -> None:
    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        approval_deadline=datetime.now(UTC) - timedelta(seconds=1),
        preview_pending=False,
    )
    _seed(tmp_queue_path, [job])

    with patch(
        "content_publisher.deadline.attest_once", new_callable=AsyncMock
    ) as attest_call:
        attest_call.return_value = None
        await deadline.deadline_checker_once(tmp_queue_path)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHING
    attest_call.assert_not_awaited()


async def test_preview_sent_future_deadline_left_alone(
    tmp_queue_path: Path, make_job
) -> None:
    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        approval_deadline=datetime.now(UTC) + timedelta(minutes=5),
        preview_pending=False,
    )
    _seed(tmp_queue_path, [job])
    await deadline.deadline_checker_once(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.PREVIEW_SENT


async def test_attest_pending_retried_via_deadline_loop(
    tmp_queue_path: Path, make_job
) -> None:
    """A job in attest-pending whose retry interval has elapsed is handed back
    to ``attest_once``.
    """
    job = make_job(
        status=JobStatus.ATTEST_PENDING,
        attest_pending_since=datetime.now(UTC) - timedelta(minutes=30),
        post_url="https://t.me/c/1/2",
        article_path=str(tmp_queue_path.parent / "a.md"),
        content_sha256="a" * 64,
        score=80,
        attest_attempts=2,
    )
    (tmp_queue_path.parent / "a.md").write_text("body\n", encoding="utf-8")
    _seed(tmp_queue_path, [job])

    with patch(
        "content_publisher.deadline.attest_once", new_callable=AsyncMock
    ) as attest_call:
        attest_call.return_value = None
        await deadline.deadline_checker_once(tmp_queue_path)
        attest_call.assert_awaited_once()
        # ``attest_once`` is called with the queue path + job_id; concrete
        # signature pinned by attest.py.
        kwargs = attest_call.await_args.kwargs
        assert kwargs.get("job_id") == job.id
        assert kwargs.get("queue_path") == tmp_queue_path


async def test_preview_sent_with_pending_preview_not_published(
    tmp_queue_path: Path, make_job
) -> None:
    """Bot hasn't actually sent the preview yet (preview_pending=true).
    The deadline-checker MUST NOT race the bot — leave the job alone until
    preview_pending flips to false.
    """
    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        approval_deadline=datetime.now(UTC) - timedelta(minutes=5),
        preview_pending=True,
    )
    _seed(tmp_queue_path, [job])
    await deadline.deadline_checker_once(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.PREVIEW_SENT
