"""Deadline-checker sub-loop (cadence 30s).

Two passes per tick:
  1) ``preview-sent`` AND ``approval_deadline <= now`` AND
     ``preview_pending == false`` → CAS ``preview-sent → publishing``.
  2) ``attest-pending`` with elapsed retry interval (5 min from last attempt)
     → call ``attest_once``.

``attest_pending_since`` is the anchor for the 24h escalation (handled inside
``attest_once``). The deadline-checker only decides "is it time to retry yet".
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from content_publisher import queue
from content_publisher.attest import attest_once
from content_publisher.models import Job, JobStatus

logger = logging.getLogger(__name__)

DEADLINE_CHECK_INTERVAL_SECONDS = int(
    os.environ.get("PUBLISH_DEADLINE_CHECK_INTERVAL_SECONDS", "30")
)
# Cadence for retrying attest-pending jobs.
ATTEST_RETRY_INTERVAL_MINUTES = int(
    os.environ.get("PUBLISH_ATTEST_RETRY_INTERVAL_MIN", "5")
)


async def deadline_checker_once(queue_path: Path) -> None:
    """Single tick of the deadline-checker. Public for testing."""
    jobs = queue.load(queue_path)
    now = datetime.now(UTC)

    for job in jobs:
        try:
            if job.status == JobStatus.PREVIEW_SENT:
                if job.preview_pending:
                    continue
                if job.approval_deadline is None:
                    continue
                if job.approval_deadline > now:
                    continue
                try:
                    queue.cas_status(
                        queue_path,
                        job.id,
                        expected=JobStatus.PREVIEW_SENT,
                        target=JobStatus.PUBLISHING,
                    )
                except queue.StaleStateError as exc:
                    logger.info(
                        "deadline: lost CAS preview-sent→publishing for %s: %s",
                        job.id,
                        exc,
                    )
            elif job.status == JobStatus.ATTEST_PENDING:
                if not _retry_due(job, now):
                    continue
                await attest_once(queue_path=queue_path, job_id=job.id)
        except Exception:  # noqa: BLE001
            logger.exception("deadline: unexpected error on job %s", job.id)


def _retry_due(job: Job, now: datetime) -> bool:
    """Return True iff the attest-pending job is due for another retry."""
    if job.attest_pending_since is None:
        return True
    # We schedule retries every N minutes from the FIRST entry into pending.
    # ``attest_attempts`` lets us compute when the next slot is.
    next_due = job.attest_pending_since + timedelta(
        minutes=ATTEST_RETRY_INTERVAL_MINUTES * max(job.attest_attempts, 1)
    )
    return bool(next_due <= now)


async def deadline_checker_loop(queue_path: Path) -> None:
    """Long-running sub-loop, cancelled on app shutdown."""
    logger.info("deadline-checker started")
    try:
        while True:
            try:
                await deadline_checker_once(queue_path)
            except Exception:  # noqa: BLE001
                logger.exception("deadline-checker tick failed")
            await asyncio.sleep(DEADLINE_CHECK_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("deadline-checker cancelled")
        raise
