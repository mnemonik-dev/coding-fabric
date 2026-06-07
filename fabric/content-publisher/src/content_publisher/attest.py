"""Attestation orchestration.

One public coroutine: ``attest_once(queue_path, job_id)``. It:
  1. loads the job, reads article bytes;
  2. calls ``mcp_client.sign_memory(...)`` (newline-delimited MCP JSON-RPC);
  3. on success: CAS ``published → done`` (first attempt) or ``attest-pending →
     done`` (retry), persisting ``attestation_hash``;
  4. on failure: CAS ``published → attest-pending`` (first attempt) or noop CAS
     ``attest-pending → attest-pending`` (bump ``attest_attempts``), EXCEPT when
     24h have elapsed from ``attest_pending_since`` — then CAS
     ``attest-pending → attest-failed`` + ``notify_pending=true``.

24h escalation clock starts at the *first* entry into attest-pending, which is
the moment ``cas_status`` auto-sets ``attest_pending_since`` (queue.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from content_publisher import queue
from content_publisher.mcp_client import MCPSignFailed, sign_memory
from content_publisher.models import Job, JobStatus

logger = logging.getLogger(__name__)

# Re-export so callers can ``from content_publisher.attest import MCPSignFailed``
__all__ = ["MCPSignFailed", "attest_once", "sign_memory_via_mcp"]


_ATTEST_ESCALATION_WINDOW = timedelta(hours=24)


# Indirection so tests can monkeypatch ``content_publisher.attest.sign_memory_via_mcp``
# without reaching into the real subprocess.
async def sign_memory_via_mcp(
    *,
    content: str,
    content_sha256: str,
    post_url: str,
    score: int,
    prompt: str,
) -> str:
    return await sign_memory(
        content=content,
        content_sha256=content_sha256,
        post_url=post_url,
        score=score,
        prompt=prompt,
    )


def _now() -> datetime:
    return datetime.now(UTC)


async def attest_once(*, queue_path: Path, job_id: str) -> None:
    """Run one attestation attempt for ``job_id``.

    No-op if the job is no longer eligible for attestation (e.g. another worker
    already moved it forward). Failures CAS the job into ``attest-pending`` or
    ``attest-failed``; the caller (worker / deadline-checker) is in charge of
    re-running this at the configured cadence.
    """
    jobs = queue.load(queue_path)
    job = next((j for j in jobs if j.id == job_id), None)
    if job is None:
        logger.warning("attest: job %s not found in queue", job_id)
        return
    if job.status not in (JobStatus.PUBLISHED, JobStatus.ATTEST_PENDING):
        logger.info(
            "attest: job %s in state %s — nothing to attest",
            job_id,
            job.status.value,
        )
        return
    if not job.article_path:
        logger.error("attest: job %s has no article_path", job_id)
        return
    if not job.post_url or not job.content_sha256 or job.score is None:
        logger.error(
            "attest: job %s missing required fields (post_url/content_sha256/score)",
            job_id,
        )
        return

    article_path = Path(job.article_path)
    try:
        content = article_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.error("attest: cannot read article for %s: %s", job_id, exc)
        # Treat as failure path to avoid wedging the job — but DO escalate
        # 24h later through the normal mechanism.
        return await _record_failure(queue_path, job, reason=f"article unreadable: {exc}")

    try:
        attestation_hash = await sign_memory_via_mcp(
            content=content,
            content_sha256=job.content_sha256,
            post_url=job.post_url,
            score=job.score,
            prompt=job.prompt,
        )
    except MCPSignFailed as exc:
        logger.warning("attest: MCP failure for %s: %s", job_id, exc)
        return await _record_failure(queue_path, job, reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("attest: unexpected error for %s", job_id)
        return await _record_failure(queue_path, job, reason=f"unexpected: {exc!r}")

    # Success path — CAS published|attest-pending → done.
    try:
        queue.cas_status(
            queue_path,
            job_id,
            expected=job.status,
            target=JobStatus.DONE,
            updates={
                "attestation_hash": attestation_hash,
                "notify_pending": True,
            },
        )
    except queue.StaleStateError as exc:
        # The transition graph forbids attest-pending → done after escalation
        # to attest-failed, so a stale CAS here means someone else already
        # acted; log and move on.
        logger.warning(
            "attest: stale CAS on success for %s: %s", job_id, exc
        )


async def _record_failure(queue_path: Path, job: Job, *, reason: str) -> None:
    """Update the queue after an MCP failure: pending → pending (bump retry)
    OR pending → failed (24h elapsed).
    """
    now = _now()
    if job.status == JobStatus.PUBLISHED:
        # First failure — CAS published → attest-pending. cas_status auto-sets
        # attest_pending_since to now.
        try:
            queue.cas_status(
                queue_path,
                job.id,
                expected=JobStatus.PUBLISHED,
                target=JobStatus.ATTEST_PENDING,
                updates={
                    "attest_attempts": (job.attest_attempts or 0) + 1,
                    "error_message": reason,
                },
            )
        except queue.StaleStateError as exc:
            logger.warning("attest: stale CAS published→pending on %s: %s", job.id, exc)
        return

    # Already in attest-pending — decide between bumping retries vs escalating.
    pending_since = job.attest_pending_since or now
    if now - pending_since >= _ATTEST_ESCALATION_WINDOW:
        try:
            queue.cas_status(
                queue_path,
                job.id,
                expected=JobStatus.ATTEST_PENDING,
                target=JobStatus.ATTEST_FAILED,
                updates={
                    "attest_attempts": (job.attest_attempts or 0) + 1,
                    "error_message": reason,
                    "notify_pending": True,
                },
            )
        except queue.StaleStateError as exc:
            logger.warning("attest: stale CAS pending→failed on %s: %s", job.id, exc)
        return

    # In-window retry: update fields in-place via a noop CAS (pending → pending
    # is not a graph transition, so we use a direct rewrite that bumps attempts).
    _bump_retry_attempts(queue_path, job.id, reason)


def _bump_retry_attempts(queue_path: Path, job_id: str, reason: str) -> None:
    """Increment ``attest_attempts`` on an already-pending job without changing
    status. cas_status() requires a state transition, so we rewrite directly
    under the same flock pattern.
    """
    from content_publisher.queue import _acquire, _lock_path, _read_lines, _release, _write_raw

    lock = _acquire(_lock_path(queue_path))
    try:
        lines = _read_lines(queue_path)
        entries: list[Job | str] = []
        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                j = Job.model_validate_json(stripped)
            except Exception:  # noqa: BLE001
                entries.append(stripped)
                continue
            if j.id == job_id and j.status == JobStatus.ATTEST_PENDING:
                as_dict = j.model_dump(mode="json")
                as_dict["attest_attempts"] = (j.attest_attempts or 0) + 1
                as_dict["error_message"] = reason
                # NOTE: attest_pending_since intentionally NOT touched — it must
                # stay frozen at the first-entry timestamp.
                entries.append(Job.model_validate(as_dict))
            else:
                entries.append(j)

        payload = (
            "\n".join(
                e.model_dump_json() if isinstance(e, Job) else e for e in entries
            )
            + "\n"
        )
        _write_raw(queue_path, payload.encode("utf-8"))
    finally:
        _release(lock)
