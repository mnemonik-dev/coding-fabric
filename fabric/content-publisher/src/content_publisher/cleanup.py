"""Cleanup-gc sub-loop (cadence 5 min).

Iterates queue.jsonl, and for each TERMINAL job with ``cleanup_at <= now``:
  1. ``shutil.rmtree(worktree_path)`` — ``FileNotFoundError`` is success;
  2. append the serialized job to ``queue.archive.jsonl`` (atomic, same flock
     pattern);
  3. remove the job from queue.jsonl.

Non-terminal jobs are never collected, even if ``cleanup_at`` is set/past.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from content_publisher.models import Job, JobStatus
from content_publisher.queue import _acquire, _lock_path, _read_lines, _release, _write_raw

logger = logging.getLogger(__name__)

CLEANUP_GC_INTERVAL_SECONDS = int(
    os.environ.get("PUBLISH_CLEANUP_GC_INTERVAL_SECONDS", "300")
)

# Source of truth for terminal status set — mirrors the empty-transitions rows
# of ``Job.allowed_transitions``. Centralised here so future reviewers can
# verify symmetry by reading one place.
_TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.DONE,
        JobStatus.REJECTED,
        JobStatus.REGENERATED,
        JobStatus.PUBLISH_FAILED,
        JobStatus.ATTEST_FAILED,
        JobStatus.FAILED,
    }
)


def _archive_path(queue_path: Path) -> Path:
    return queue_path.with_name("queue.archive.jsonl")


def _append_archive(archive_path: Path, job: Job) -> None:
    """Atomic-replace append: read existing, add new line, atomic rename.

    Same pattern as ``queue.append_job`` so a concurrent reader sees either
    pre-append or post-append, never a half-written line.

    Idempotency note: ``cleanup_gc_once`` archives BEFORE rewriting queue.jsonl,
    so a crash between the two ops will replay on the next tick and re-archive
    the same job. The archive may carry a duplicate for that job — this is
    deliberate forensic noise, not silent dedup. Operators wanting clean archive
    output should de-duplicate by ``id`` at read time.
    """
    existing: list[bytes] = []
    if archive_path.exists():
        existing = [
            ln.encode("utf-8")
            for ln in archive_path.read_text().splitlines()
            if ln.strip()
        ]
    existing.append(job.model_dump_json().encode("utf-8"))
    payload = b"\n".join(existing) + b"\n"
    _write_raw(archive_path, payload)


def _rmtree_safe(worktree_path: str | None) -> None:
    if not worktree_path:
        return
    p = Path(worktree_path)
    try:
        shutil.rmtree(p, ignore_errors=False)
    except FileNotFoundError:
        # Already gone — operator may have cleaned manually. Archive anyway.
        return
    except OSError as exc:
        logger.warning("cleanup: rmtree(%s) failed: %s", p, exc)


def cleanup_gc_once(queue_path: Path) -> None:
    """Single tick. Public for testing."""
    now = datetime.now(UTC)
    lock = _acquire(_lock_path(queue_path))
    try:
        lines = _read_lines(queue_path)
        survivors: list[Job | str] = []
        to_archive: list[Job] = []

        for raw in lines:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                j = Job.model_validate_json(stripped)
            except Exception:  # noqa: BLE001
                # Preserve verbatim — never silently drop unknown lines.
                survivors.append(stripped)
                continue
            if (
                j.status in _TERMINAL_STATUSES
                and j.cleanup_at is not None
                and j.cleanup_at <= now
            ):
                to_archive.append(j)
            else:
                survivors.append(j)

        if not to_archive:
            return

        # Archive first, THEN rewrite queue.jsonl — if we crash between the two,
        # the next tick will replay (idempotent: the same job is still in
        # queue.jsonl with cleanup_at in the past, gets re-archived; archive
        # may carry a duplicate but that's fine for forensics).
        archive_path = _archive_path(queue_path)
        for j in to_archive:
            _append_archive(archive_path, j)
            _rmtree_safe(j.worktree_path)

        payload = (
            "\n".join(
                e.model_dump_json() if isinstance(e, Job) else e for e in survivors
            )
            + ("\n" if survivors else "")
        )
        _write_raw(queue_path, payload.encode("utf-8"))
    finally:
        _release(lock)


async def cleanup_gc_loop(queue_path: Path) -> None:
    logger.info("cleanup-gc started")
    try:
        while True:
            try:
                cleanup_gc_once(queue_path)
            except Exception:  # noqa: BLE001
                logger.exception("cleanup-gc tick failed")
            await asyncio.sleep(CLEANUP_GC_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("cleanup-gc cancelled")
        raise
