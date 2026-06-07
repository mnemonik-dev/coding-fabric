"""Attestation retry cadence + 24h escalation.

Cadence: every 5 minutes from ``attest_pending_since``. Escalation to
``attest-failed`` happens when ``now - attest_pending_since >= 24h``.

The 24h clock starts at the *first* entry into attest-pending — NOT at
``created_at``. ``attest_pending_since`` is written once and never updated on
subsequent retries (responsibility lives in ``queue.cas_status``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from content_publisher import attest, queue
from content_publisher.models import JobStatus

pytestmark = pytest.mark.asyncio


def _seed(path: Path, job) -> None:
    path.write_text(job.model_dump_json() + "\n", encoding="utf-8")


async def test_first_attest_failure_records_pending_since(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """First failure: published → attest-pending. ``attest_pending_since`` set."""
    article = tmp_path / "a.md"
    article.write_text("body\n", encoding="utf-8")
    job = make_job(
        status=JobStatus.PUBLISHED,
        article_path=str(article),
        post_url="https://t.me/c/1/2",
        content_sha256="a" * 64,
        score=80,
        published_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    _seed(tmp_queue_path, job)

    with patch(
        "content_publisher.attest.sign_memory_via_mcp", new_callable=AsyncMock
    ) as mcp:
        mcp.side_effect = attest.MCPSignFailed("mcp down")
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.ATTEST_PENDING
    assert refreshed.attest_pending_since is not None
    assert refreshed.attest_attempts == 1


async def test_retry_does_not_overwrite_attest_pending_since(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """Second failure must NOT bump ``attest_pending_since``."""
    article = tmp_path / "a.md"
    article.write_text("body\n", encoding="utf-8")
    original_since = datetime.now(UTC) - timedelta(minutes=10)
    job = make_job(
        status=JobStatus.ATTEST_PENDING,
        article_path=str(article),
        post_url="https://t.me/c/1/2",
        content_sha256="a" * 64,
        attest_pending_since=original_since,
        attest_attempts=1,
        score=80,
        published_at=datetime.now(UTC) - timedelta(minutes=11),
    )
    _seed(tmp_queue_path, job)

    with patch(
        "content_publisher.attest.sign_memory_via_mcp", new_callable=AsyncMock
    ) as mcp:
        mcp.side_effect = attest.MCPSignFailed("still down")
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.ATTEST_PENDING
    assert refreshed.attest_pending_since == original_since, (
        "attest_pending_since must be sticky once set"
    )
    assert refreshed.attest_attempts == 2


async def test_retry_cadence_5min_then_24h_escalation(
    tmp_queue_path: Path, tmp_path: Path, make_job
) -> None:
    """After 24h in attest-pending, the next attempt escalates to attest-failed.
    notify_pending=true is set so the bot can DM the operator.
    """
    article = tmp_path / "a.md"
    article.write_text("body\n", encoding="utf-8")
    twenty_five_hours_ago = datetime.now(UTC) - timedelta(hours=25)
    job = make_job(
        status=JobStatus.ATTEST_PENDING,
        article_path=str(article),
        post_url="https://t.me/c/1/2",
        content_sha256="a" * 64,
        attest_pending_since=twenty_five_hours_ago,
        attest_attempts=20,
        score=80,
        published_at=twenty_five_hours_ago - timedelta(seconds=10),
    )
    _seed(tmp_queue_path, job)

    with patch(
        "content_publisher.attest.sign_memory_via_mcp", new_callable=AsyncMock
    ) as mcp:
        mcp.side_effect = attest.MCPSignFailed("still down")
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.ATTEST_FAILED
    assert refreshed.notify_pending is True


async def test_happy_path_published_to_done(
    tmp_queue_path: Path, tmp_path: Path, make_job
) -> None:
    article = tmp_path / "a.md"
    article.write_text("body\n", encoding="utf-8")
    job = make_job(
        status=JobStatus.PUBLISHED,
        article_path=str(article),
        post_url="https://t.me/c/1/2",
        content_sha256="a" * 64,
        score=80,
        published_at=datetime.now(UTC),
    )
    _seed(tmp_queue_path, job)

    with patch(
        "content_publisher.attest.sign_memory_via_mcp", new_callable=AsyncMock
    ) as mcp:
        mcp.return_value = "deadbeef" * 8  # 64-hex string
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.DONE
    assert refreshed.attestation_hash == "deadbeef" * 8
    assert refreshed.notify_pending is True
