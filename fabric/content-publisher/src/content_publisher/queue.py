"""SHARED CAS primitive for queue.jsonl.

Both the long-running worker (Tasks 5-6) and the telegram bot (Task 8) import
these four functions. No other writer to ``queue.jsonl`` may exist — that is
the invariant that makes the race "operator click + deadline fire" safe.

Implementation: ``fcntl.flock(LOCK_EX)`` on a sibling ``.lock`` file +
read-modify-write + ``os.replace`` (POSIX-atomic). Tech-spec Decision 2.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from content_publisher.models import Job, JobStatus
from content_publisher.state import _acquire, _release, _write_raw

logger = logging.getLogger(__name__)

# First 12 characters of a canonical UUID v4 string: 8 hex + dash + 3 hex.
# This is the slice the bot stores in callback_data (``job.id[:12]``).
PREFIX12_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{3}$")


class StaleStateError(Exception):
    """Raised when ``cas_status`` finds the job's actual status differs from ``expected``.

    Carries ``actual_status`` so the caller can decide whether to retry, log,
    or report "too late" to the operator.
    """

    def __init__(self, job_id: str, expected: JobStatus, actual_status: JobStatus) -> None:
        super().__init__(
            f"stale CAS on job {job_id}: expected {expected.value}, "
            f"found {actual_status.value}"
        )
        self.job_id = job_id
        self.expected = expected
        self.actual_status = actual_status


class IllegalTransitionError(Exception):
    """Raised when ``target`` is not in ``allowed_transitions[expected]``."""


class JobNotFoundError(Exception):
    """Raised when no job with the given id exists in the queue."""


def _lock_path(queue_path: Path) -> Path:
    return Path(str(queue_path) + ".lock")


def _read_lines(queue_path: Path) -> list[str]:
    if not queue_path.exists():
        return []
    return queue_path.read_text().splitlines()


def _parse_lines(lines: list[str]) -> list[Job]:
    jobs: list[Job] = []
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            jobs.append(Job.model_validate_json(stripped))
        except Exception as exc:  # noqa: BLE001 — broad on purpose: any parse error
            logger.warning("queue: skipping malformed line %d: %s", idx, exc)
    return jobs


def append_job(
    queue_path: Path,
    *,
    prompt: str,
    mode: Literal["approval", "auto"] | None,
    chat_id: int | None,
    thread_id: int | None,
    reply_to_message_id: int | None,
    fire_at: datetime | None = None,
) -> Job:
    """Create a new ``queued`` Job and atomically append it to the queue."""
    resolved_mode: Literal["approval", "auto"] = mode if mode is not None else "approval"
    job = Job(
        id=str(uuid.uuid4()),
        created_at=datetime.now(UTC),
        fire_at=fire_at,
        prompt=prompt,
        mode=resolved_mode,
        status=JobStatus.QUEUED,
        chat_id=chat_id,
        thread_id=thread_id,
        reply_to_message_id=reply_to_message_id,
    )

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire(_lock_path(queue_path))
    try:
        # Build full new content under lock so a concurrent reader either sees
        # the file pre-append or post-append, never half-written. ``a`` mode
        # would be cheaper but is not safe for lines > PIPE_BUF on ext4.
        existing = _read_lines(queue_path)
        existing_nonblank = [ln for ln in existing if ln.strip()]
        new_payload = "\n".join(existing_nonblank + [job.model_dump_json()]) + "\n"
        _write_raw(queue_path, new_payload.encode("utf-8"))
    finally:
        _release(lock)

    return job


def load(queue_path: Path) -> list[Job]:
    """Return all jobs in the queue. Blank / malformed lines are skipped (warn)."""
    return _parse_lines(_read_lines(queue_path))


def cas_status(
    queue_path: Path,
    job_id: str,
    *,
    expected: JobStatus,
    target: JobStatus,
    updates: dict[str, Any] | None = None,
) -> Job:
    """Atomically transition ``job_id`` from ``expected`` to ``target``.

    Auto-set timestamps (applied BEFORE caller-supplied ``updates``):
      * target == published        -> published_at = now(UTC)
      * target == attest-pending   -> attest_pending_since = now(UTC)

    Caller-supplied ``updates`` may overwrite auto-set values (useful for tests
    or backfill).

    Terminal-error states (failed / publish-failed / attest-failed) do not
    require an ``error_message`` in ``updates`` for the primitive to succeed —
    that contract is enforced by Tasks 5/6 at their call sites — but if one IS
    supplied it is persisted verbatim.
    """
    # Static graph check — no I/O needed and the result cannot change at
    # runtime. The live-status comparison below (StaleStateError) is the part
    # that requires the lock.
    transitions = Job.allowed_transitions()
    if target not in transitions.get(expected, set()):
        raise IllegalTransitionError(
            f"illegal transition {expected.value} -> {target.value}"
        )

    lock = _acquire(_lock_path(queue_path))
    try:
        lines = _read_lines(queue_path)
        # Preserve every line we cannot parse as an opaque pass-through so the
        # CAS rewrite never silently deletes data we don't understand (e.g. a
        # half-written line left by a kill -9 mid-_write_raw). Blank lines are
        # dropped — they are noise, not data.
        entries: list[Job | str] = []
        idx_match: int | None = None
        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                j = Job.model_validate_json(stripped)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "queue: preserving malformed line during cas (will be kept verbatim): %s",
                    exc,
                )
                entries.append(stripped)
                continue
            if j.id == job_id and idx_match is None:
                idx_match = len(entries)
            entries.append(j)

        if idx_match is None:
            raise JobNotFoundError(job_id)

        current = entries[idx_match]
        assert isinstance(current, Job)  # idx_match only set for parsed jobs
        if current.status != expected:
            raise StaleStateError(job_id, expected, current.status)

        as_dict = current.model_dump(mode="json")
        as_dict["status"] = target.value

        now = datetime.now(UTC).isoformat()
        if target == JobStatus.PUBLISHED:
            as_dict["published_at"] = now
        elif target == JobStatus.ATTEST_PENDING:
            as_dict["attest_pending_since"] = now

        if updates:
            as_dict.update(updates)

        # Re-validate so caller errors (bad type in updates) fail loud here,
        # not at next ``load()``.
        new_job = Job.model_validate(as_dict)
        entries[idx_match] = new_job

        payload = (
            "\n".join(
                e.model_dump_json() if isinstance(e, Job) else e for e in entries
            )
            + "\n"
        )
        _write_raw(queue_path, payload.encode("utf-8"))
        return new_job
    finally:
        _release(lock)


def find_by_prefix(queue_path: Path, job_id_prefix12: str) -> Job | None:
    """Resolve a 12-hex-char id-prefix back to a full Job.

    Telegram ``callback_data`` is capped at 64 bytes; the bot stores
    ``job.id[:12]`` — i.e. the first 8 hex + ``-`` + next 3 hex of the canonical
    UUID v4 string. Returns ``None`` on miss; raises ``ValueError`` on ambiguous
    prefix (statistically near-impossible for UUID v4 but treated as fail-loud:
    silently picking the first match could publish the wrong operator's content
    from a misrouted callback).
    """
    if len(job_id_prefix12) != 12 or not PREFIX12_RE.fullmatch(job_id_prefix12):
        raise ValueError(
            f"job_id_prefix12 must be the first 12 chars of a UUID v4 "
            f"(8 hex + '-' + 3 hex), got: {job_id_prefix12!r}"
        )

    matches = [j for j in load(queue_path) if j.id.startswith(job_id_prefix12)]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"ambiguous prefix: {len(matches)} matches")
    return matches[0]
