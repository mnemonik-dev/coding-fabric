"""Worker step for ``queued -> writing -> scoring -> preview-sent``.

One public coroutine: ``run_writing_to_preview(job)``. It:
  1. validates ``job.id`` as a canonical UUID v4 and resolves a worktree path
     with strict containment under ``WORKTREE_ROOT``;
  2. CAS ``queued -> writing`` (silently no-ops if another worker beat us);
  3. spawns ``claude`` (``spawn.spawn_claude``);
  4. CAS ``writing -> scoring``;
  5. runs ``analyze_blog`` (``score.analyze``) and renders the preview
     (``render.render_preview``);
  6. CAS ``scoring -> preview-sent`` writing ``score``, ``issues`` (truncated
     to 3 by ``score.analyze``), ``preview_segments``, ``approval_deadline``,
     ``preview_pending=True``, ``cleanup_at`` in a single CAS write.

Any step-level error (``SpawnFailed``, ``ScoreFailed``, render exception, etc.)
moves the job to ``failed`` with ``notify_pending=True`` and a diagnostic
``error_message``. The worktree is intentionally NOT removed — cleanup-gc
(Task 6) collects it on ``cleanup_at``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from content_publisher import queue, score, spawn
from content_publisher.models import UUID4_RE, Job, JobStatus
from content_publisher.render import render_preview

logger = logging.getLogger(__name__)

# Module-level config — read at import time from env so tests can ``monkeypatch``
# both these names AND the env values cleanly. Defaults match Task 3 deploy
# layout. Tests rebind these directly.
WORKTREE_ROOT = Path(
    os.environ.get("CONTENT_PUBLISHER_WORKTREE_ROOT", "/var/lib/content-publisher/work")
)
QUEUE_PATH = Path(
    os.environ.get("CONTENT_PUBLISHER_QUEUE", "/var/lib/content-publisher/queue.jsonl")
)
APPROVAL_TIMEOUT_MIN = int(os.environ.get("PUBLISH_APPROVAL_TIMEOUT_MIN", "5"))
# Exposed for tests asserting the high-score path actually crossed the gate
# (vs. silently passing because the gate was ignored).
MNEMONIK_MIN_SCORE = int(os.environ.get("MNEMONIK_MIN_SCORE", "80"))
CLEANUP_AFTER_HOURS = 24


def _validate_uuid_v4(job_id: str) -> None:
    """Raise ValueError unless ``job_id`` is a canonical lowercase UUID v4.

    Uses the same regex as ``Job.id``'s field validator so the worker and the
    Pydantic model have strictly identical acceptance sets. (``uuid.UUID()``
    is laxer — it tolerates braces, uppercase, and whitespace.)
    """
    if not UUID4_RE.fullmatch(job_id):
        raise ValueError(
            f"job.id is not a canonical lowercase UUID v4: {job_id!r}"
        )


def _resolve_worktree(job_id: str) -> Path:
    """Resolve and verify path containment under ``WORKTREE_ROOT``.

    ``job_id`` has already been validated as a UUID v4, so it cannot contain
    ``..``, ``/``, or control chars — but we still enforce containment as
    defense-in-depth.
    """
    root = WORKTREE_ROOT.resolve()
    candidate = (WORKTREE_ROOT / job_id).resolve()
    if candidate.parent != root:
        raise ValueError(
            f"worktree path escapes root: {candidate} not under {root}"
        )
    return candidate


def _mark_failed(job_id: str, current_status: JobStatus, reason: str) -> None:
    """Best-effort CAS to ``failed`` with ``notify_pending=True`` + ``error_message``.

    If another worker raced us and already moved the job, we just log and
    return — the other worker is responsible for its own bookkeeping.
    """
    try:
        queue.cas_status(
            QUEUE_PATH,
            job_id,
            expected=current_status,
            target=JobStatus.FAILED,
            updates={
                "notify_pending": True,
                "error_message": reason,
            },
        )
    except (queue.StaleStateError, queue.JobNotFoundError) as exc:
        logger.warning(
            "worker: could not mark job %s as failed from %s: %s",
            job_id,
            current_status.value,
            exc,
        )


async def run_writing_to_preview(job: Job) -> None:
    """Drive one job from ``queued`` all the way to ``preview-sent`` (or ``failed``)."""
    _validate_uuid_v4(job.id)
    worktree = _resolve_worktree(job.id)

    # 1) queued -> writing — claim the job BEFORE we touch the filesystem.
    # If another worker beats us in the CAS, we never create the worktree
    # directory, so there is no leaked artifact for cleanup-gc to miss.
    try:
        queue.cas_status(
            QUEUE_PATH,
            job.id,
            expected=JobStatus.QUEUED,
            target=JobStatus.WRITING,
            updates={"worktree_path": str(worktree)},
        )
    except queue.StaleStateError:
        # Another worker already claimed it; nothing to do.
        logger.info("worker: job %s already claimed, skipping", job.id)
        return

    # We own the job — now create the worktree (idempotent on crash-recovery
    # restart: the directory may already exist from a prior writing attempt).
    worktree.mkdir(parents=True, exist_ok=True)

    # 2) writing: spawn claude
    try:
        article_path = await spawn.spawn_claude(prompt=job.prompt, worktree=worktree)
    except spawn.SpawnFailed as exc:
        _mark_failed(
            job.id,
            JobStatus.WRITING,
            f"claude failed: {exc} | stderr: {exc.stderr_tail}",
        )
        return
    except Exception as exc:  # noqa: BLE001 — surface any unexpected failure as ``failed``
        _mark_failed(job.id, JobStatus.WRITING, f"unexpected error in writing: {exc!r}")
        return

    # Defense-in-depth: refuse to score an article that escaped the worktree
    # (claude shouldn't write outside cwd, but we re-validate).
    resolved_article = article_path.resolve()
    if not resolved_article.is_relative_to(worktree):
        _mark_failed(
            job.id,
            JobStatus.WRITING,
            f"article path escapes worktree: {resolved_article}",
        )
        return

    # 3) writing -> scoring
    try:
        queue.cas_status(
            QUEUE_PATH,
            job.id,
            expected=JobStatus.WRITING,
            target=JobStatus.SCORING,
            updates={"article_path": str(resolved_article)},
        )
    except queue.StaleStateError as exc:
        logger.warning("worker: stale CAS writing->scoring on %s: %s", job.id, exc)
        return

    # 4) scoring: analyze + render
    try:
        score_result = await score.analyze(resolved_article)
    except score.ScoreFailed as exc:
        _mark_failed(
            job.id,
            JobStatus.SCORING,
            f"analyze_blog failed: {exc} | stderr: {exc.stderr_tail}",
        )
        return
    except Exception as exc:  # noqa: BLE001
        _mark_failed(job.id, JobStatus.SCORING, f"unexpected error in scoring: {exc!r}")
        return

    try:
        preview_segments = render_preview(resolved_article)
    except Exception as exc:  # noqa: BLE001
        _mark_failed(
            job.id,
            JobStatus.SCORING,
            f"render_preview failed: {exc!r}",
        )
        return

    # 5) scoring -> preview-sent, with deadline + cleanup + preview metadata
    # all in the SAME cas write (Task 6's deadline-checker races us otherwise).
    now = datetime.now(UTC)
    approval_deadline = (now + timedelta(minutes=APPROVAL_TIMEOUT_MIN)).isoformat()
    cleanup_at = (now + timedelta(hours=CLEANUP_AFTER_HOURS)).isoformat()
    # In auto-mode the operator surrendered the preview gate, so we set
    # preview_pending=False (bot has nothing to render) and immediately
    # transition into PUBLISHING. The state graph forbids skipping PREVIEW_SENT,
    # so this is two CASes back-to-back, both winnable since no other actor
    # observes the intermediate state.
    auto_mode = job.mode == "auto"
    try:
        queue.cas_status(
            QUEUE_PATH,
            job.id,
            expected=JobStatus.SCORING,
            target=JobStatus.PREVIEW_SENT,
            updates={
                "score": score_result.score,
                "issues": score_result.issues,
                "preview_segments": preview_segments,
                "approval_deadline": approval_deadline,
                "preview_pending": not auto_mode,
                "cleanup_at": cleanup_at,
            },
        )
    except queue.StaleStateError as exc:
        logger.warning(
            "worker: stale CAS scoring->preview-sent on %s: %s", job.id, exc
        )
        return

    if auto_mode:
        try:
            queue.cas_status(
                QUEUE_PATH,
                job.id,
                expected=JobStatus.PREVIEW_SENT,
                target=JobStatus.PUBLISHING,
            )
        except queue.StaleStateError as exc:
            # In auto-mode the worker and the 30s deadline-checker both race
            # to take preview-sent → publishing — exactly one wins. The loser
            # is EXPECTED here (the deadline-checker fired in the brief 2-CAS
            # gap), so this is diagnostic noise, not an error. Logged at DEBUG.
            logger.debug(
                "worker: lost CAS preview-sent->publishing (auto) on %s "
                "to deadline-checker — expected: %s",
                job.id,
                exc,
            )
