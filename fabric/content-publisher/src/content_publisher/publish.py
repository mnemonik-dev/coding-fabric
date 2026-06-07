"""Publish step: ``publishing → published`` or ``publishing → publish-failed``.

Order of operations (Decision 8 + AC b/c/d/e):
  1. Posts-per-hour rate ceiling check (security R2-9). Over-ceiling →
     CAS ``publishing → publish-failed`` without an in-process call.
  2. Write ``tentative_publish_started_at = now`` to queue.jsonl. This is a
     forensic marker for the rare crash-mid-publish double-post case.
  3. Build ``Settings`` (see _build_settings) and call
     ``run_campaign_from_article(...)`` strictly keyword-only, ``attest=False``
     (we attest via MCP-stdio, Decision 8).
  4. ``pr = result.results[0]``:
       * ``pr.ok``: CAS ``publishing → published`` with post_url, ids,
         content_sha256.
       * not ``pr.ok``: CAS ``publishing → publish-failed`` with pr.error +
         notify_pending=true.

The mnemonik_blogger import is deferred to call time so dev-venvs without the
pinned upstream snapshot can still collect tests for the rest of this module.
Decision 11's install-time signature assertion is the production gate.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from content_publisher import queue
from content_publisher.models import JobStatus

logger = logging.getLogger(__name__)


# Module-level config — bound at import time. Tests rebind directly.
QUEUE_PATH = Path(
    os.environ.get("CONTENT_PUBLISHER_QUEUE", "/var/lib/content-publisher/queue.jsonl")
)
POSTS_PER_HOUR_CEILING = int(os.environ.get("MNEMONIK_POSTS_PER_HOUR_CEILING", "20"))


def _run_campaign_from_article(**kwargs: Any) -> Any:
    """Indirection so tests can patch the upstream call without monkeypatching
    every import path. In production, this binds to the keyword-only function
    ``mnemonik_blogger.agent.run_campaign_from_article``.
    """
    from mnemonik_blogger.agent import run_campaign_from_article  # local import

    return run_campaign_from_article(**kwargs)


def _min_score_env() -> int:
    return int(os.environ.get("MNEMONIK_MIN_SCORE", "80"))


def _build_settings() -> Any:
    """Construct a ``Settings`` instance from environment.

    Field naming/typing pinned by the upstream snapshot installed under
    ``/opt/blogger/venv/`` and verified by Decision 11's import + signature
    assertion at deploy time. If the snapshot diverges from this construction,
    fail loud at first call rather than silently shipping with wrong settings.
    """
    from mnemonik_blogger.config import Settings, TelegramConfig
    from pydantic import SecretStr

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_CHANNEL", "@mnemonik")
    claude_blog_path = os.environ.get("MNEMONIK_CLAUDE_BLOG_PATH", "/opt/claude-blog")

    return Settings(
        dry_run=False,
        min_score=_min_score_env(),
        claude_blog_path=claude_blog_path,
        telegram=TelegramConfig(
            bot_token=SecretStr(bot_token),
            channel=channel,
        ),
    )


def _compute_sha256(article_path: Path) -> str:
    return hashlib.sha256(article_path.read_bytes()).hexdigest()


def _count_published_last_hour(jobs: list[Any]) -> int:
    """Count jobs with ``status==published`` (or terminally further) whose
    ``published_at`` is within the last 60 minutes.

    "Or terminally further" matters: a job that progressed published → done
    still consumed a publish slot in the last hour.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    counted_statuses = {
        JobStatus.PUBLISHED,
        JobStatus.ATTEST_PENDING,
        JobStatus.DONE,
        JobStatus.ATTEST_FAILED,
    }
    n = 0
    for j in jobs:
        if j.status not in counted_statuses:
            continue
        if j.published_at is None:
            continue
        if j.published_at >= cutoff:
            n += 1
    return n


async def publish_step(*, job_id: str) -> None:
    """Drive one job through the publish step.

    Raises whatever the upstream blogger call raises (so the worker / recovery
    can log it). State transitions are CAS'd via ``queue.cas_status`` so any
    race with the deadline-checker or bot is safe.
    """
    jobs = queue.load(QUEUE_PATH)
    job = next((j for j in jobs if j.id == job_id), None)
    if job is None:
        logger.warning("publish: job %s not found", job_id)
        return
    if job.status != JobStatus.PUBLISHING:
        logger.info(
            "publish: job %s no longer in publishing (now %s); skipping",
            job_id,
            job.status.value,
        )
        return
    if not job.article_path:
        reason = "missing article_path"
        logger.error("publish: job %s has no article_path", job_id)
        try:
            queue.cas_status(
                QUEUE_PATH,
                job_id,
                expected=JobStatus.PUBLISHING,
                target=JobStatus.PUBLISH_FAILED,
                updates={
                    "publish_error": reason,
                    "notify_pending": True,
                },
            )
        except queue.StaleStateError as exc:
            logger.warning(
                "publish: stale CAS publishing→failed for missing article_path on %s: %s",
                job_id,
                exc,
            )
        return

    # 1) Rate-ceiling pre-check (security R2-9).
    posted_last_hour = _count_published_last_hour(jobs)
    if posted_last_hour >= POSTS_PER_HOUR_CEILING:
        try:
            queue.cas_status(
                QUEUE_PATH,
                job_id,
                expected=JobStatus.PUBLISHING,
                target=JobStatus.PUBLISH_FAILED,
                updates={
                    "publish_error": "rate ceiling exceeded",
                    "notify_pending": True,
                },
            )
        except queue.StaleStateError as exc:
            logger.warning("publish: stale CAS on rate-ceiling for %s: %s", job_id, exc)
        return

    # 2) Forensic marker BEFORE the call (AC-T7).
    # If we crash between this write and the call landing in Telegram, the
    # operator uses this timestamp to identify the duplicate in the channel.
    now = datetime.now(UTC)
    try:
        queue.update_job_fields(
            QUEUE_PATH,
            job_id,
            expected_status=JobStatus.PUBLISHING,
            updates={"tentative_publish_started_at": now.isoformat()},
        )
    except queue.StaleStateError:
        # Lost the CAS to someone else — give up cleanly.
        return

    article = Path(job.article_path)

    # 3) The in-process call. Strictly keyword-only. NO ``dry_run=`` kwarg.
    from mnemonik_blogger.config import Platform  # deferred import — Decision 11

    settings = _build_settings()
    result = _run_campaign_from_article(
        article=article,
        platforms=[Platform.TELEGRAM],
        settings=settings,
        attest=False,
        min_score=_min_score_env(),
    )

    # 4) Read result.results[0] (NOT .posts[0] — tech-spec Decision 4).
    pr = result.results[0]

    if pr.ok:
        try:
            queue.cas_status(
                QUEUE_PATH,
                job_id,
                expected=JobStatus.PUBLISHING,
                target=JobStatus.PUBLISHED,
                updates={
                    "post_url": pr.primary_url,
                    "post_message_ids": list(pr.ids or []),
                    "content_sha256": _compute_sha256(article),
                    # NOTE: notify_pending is deliberately NOT set here. User-spec
                    # step 9 calls for ONE consolidated "Опубликовано. Пост: <link>.
                    # Receipt: <hash>." message after attestation completes; setting
                    # notify_pending here would fire a premature notify without the
                    # receipt. attest.attest_once raises notify_pending on its own
                    # success/failure terminals (DONE / ATTEST_PENDING / ATTEST_FAILED).
                },
            )
        except queue.StaleStateError as exc:
            logger.warning("publish: stale CAS publishing→published for %s: %s", job_id, exc)
    else:
        try:
            queue.cas_status(
                QUEUE_PATH,
                job_id,
                expected=JobStatus.PUBLISHING,
                target=JobStatus.PUBLISH_FAILED,
                updates={
                    "publish_error": pr.error or "unknown publisher error",
                    "notify_pending": True,
                },
            )
        except queue.StaleStateError as exc:
            logger.warning("publish: stale CAS publishing→failed for %s: %s", job_id, exc)
