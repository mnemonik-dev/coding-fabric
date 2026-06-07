"""Crash recovery on worker startup (Decision 8).

Per in-flight status, deterministically:
  * writing / scoring         → reset to queued (restart from top).
  * preview-sent              → leave (deadline-checker decides).
  * publishing                → leave (publish-step retries normally;
                                       tentative_publish_started_at is the
                                       operator's duplicate-detection hint).
  * attest-pending            → leave (deadline-checker reschedules).
  * terminal                  → leave (cleanup-gc owns).

No ``recovery-needed`` state. No slash commands. This is the entire flow.

The writing/scoring resets bypass ``cas_status`` because the in-progress→queued
transitions are not in the normal state graph — they are explicitly a recovery
move that only the boot phase makes (queue.jsonl is single-writer at this point;
no concurrent CAS to race). We still take the flock so a redundant launch on a
shared volume cannot interleave.
"""

from __future__ import annotations

import logging
from pathlib import Path

from content_publisher.models import Job, JobStatus
from content_publisher.queue import (
    _acquire,
    _lock_path,
    _read_lines,
    _release,
    _write_raw,
)

logger = logging.getLogger(__name__)


_RESET_TO_QUEUED: frozenset[JobStatus] = frozenset(
    {JobStatus.WRITING, JobStatus.SCORING}
)


def recover_in_flight(queue_path: Path) -> None:
    """Synchronous startup helper. Run BEFORE the three sub-loops fan out."""
    if not queue_path.exists():
        logger.info("recovery: queue file does not exist — nothing to do")
        return

    lock = _acquire(_lock_path(queue_path))
    try:
        lines = _read_lines(queue_path)
        if not lines:
            return

        rewritten = False
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
            if j.status in _RESET_TO_QUEUED:
                as_dict = j.model_dump(mode="json")
                as_dict["status"] = JobStatus.QUEUED.value
                # Clear scoring/writing artefacts that are no longer relevant
                # — score/issues/preview_segments belong to a completed
                # writing pass and would mislead the next run.
                as_dict["score"] = None
                as_dict["issues"] = []
                as_dict["preview_segments"] = []
                entries.append(Job.model_validate(as_dict))
                logger.info("recovery: %s %s→queued", j.id, j.status.value)
                rewritten = True
            else:
                entries.append(j)

        if not rewritten:
            return

        payload = (
            "\n".join(
                e.model_dump_json() if isinstance(e, Job) else e for e in entries
            )
            + "\n"
        )
        _write_raw(queue_path, payload.encode("utf-8"))
    finally:
        _release(lock)

