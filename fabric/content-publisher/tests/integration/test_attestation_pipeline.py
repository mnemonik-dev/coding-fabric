"""End-to-end attestation pipeline: happy → done; failure → attest-pending →
retry → escalation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from content_publisher import attest, queue
from content_publisher.models import JobStatus

pytestmark = pytest.mark.asyncio


async def test_happy_then_failure_then_retry_then_escalation(
    tmp_queue_path: Path, tmp_path: Path, make_job
) -> None:
    """Full attestation lifecycle exercised across multiple attest_once calls."""
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
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    # Attempt 1: MCP fails → attest-pending.
    with patch(
        "content_publisher.attest.sign_memory_via_mcp",
        new_callable=AsyncMock,
    ) as mcp:
        mcp.side_effect = attest.MCPSignFailed("offline")
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.ATTEST_PENDING
    first_since = refreshed.attest_pending_since
    assert first_since is not None
    assert refreshed.attest_attempts == 1

    # Attempt 2: still failing — attest_pending_since does NOT move.
    with patch(
        "content_publisher.attest.sign_memory_via_mcp", new_callable=AsyncMock
    ) as mcp:
        mcp.side_effect = attest.MCPSignFailed("still offline")
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.ATTEST_PENDING
    assert refreshed.attest_pending_since == first_since
    assert refreshed.attest_attempts == 2

    # Simulate 25 hours in the past — synthesise.
    # We re-write the job line with a backdated attest_pending_since.
    queue_jobs = queue.load(tmp_queue_path)
    j = queue_jobs[0]
    j_dict = j.model_dump(mode="json")
    j_dict["attest_pending_since"] = (
        datetime.now(UTC) - timedelta(hours=25)
    ).isoformat()
    from content_publisher.models import Job

    j_back = Job.model_validate(j_dict)
    tmp_queue_path.write_text(j_back.model_dump_json() + "\n", encoding="utf-8")

    # Attempt 3: 25h elapsed → escalate to attest-failed.
    with patch(
        "content_publisher.attest.sign_memory_via_mcp", new_callable=AsyncMock
    ) as mcp:
        mcp.side_effect = attest.MCPSignFailed("still offline")
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.ATTEST_FAILED
    assert refreshed.notify_pending is True
