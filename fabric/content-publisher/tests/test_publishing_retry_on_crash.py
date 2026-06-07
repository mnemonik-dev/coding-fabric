"""AC-T13: a job stuck in ``publishing`` on startup is simply put back on the
normal publish path. No ``recovery-needed`` state, no slash commands.

Decision 8 — accept rare double-post; reduce LOC + state surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from content_publisher import queue
from content_publisher.models import JobStatus


def test_recovery_needed_state_does_not_exist() -> None:
    """Hard gate: the literal string ``recovery-needed`` must not be a JobStatus value."""
    assert "recovery-needed" not in {s.value for s in JobStatus}
    assert "recovery_needed" not in {s.name.lower() for s in JobStatus}


def test_publishing_status_just_retries_normal_path(
    tmp_queue_path: Path, make_job
) -> None:
    """``recover_in_flight`` MUST leave a ``publishing`` job in ``publishing``
    so the normal publish-step picks it up at the next worker tick.
    """
    from content_publisher import recovery

    article = tmp_queue_path.parent / "article.md"
    article.write_text("body\n", encoding="utf-8")

    job = make_job(
        status=JobStatus.PUBLISHING,
        article_path=str(article),
        tentative_publish_started_at=datetime.now(UTC),
    )
    # Seed the queue with a publishing job directly.
    tmp_queue_path.write_text(job.model_dump_json() + "\n")

    recovery.recover_in_flight(tmp_queue_path)

    after = queue.load(tmp_queue_path)
    assert len(after) == 1
    refreshed = after[0]
    assert refreshed.status == JobStatus.PUBLISHING, (
        "Decision 8: publishing-on-crash = just retry; NO recovery-needed state."
    )
    # tentative_publish_started_at must be preserved verbatim (forensic field).
    assert refreshed.tentative_publish_started_at == job.tentative_publish_started_at


def test_recover_in_flight_writing_to_queued(tmp_queue_path: Path, make_job) -> None:
    """``writing`` jobs become ``queued`` again — restart from the top."""
    from content_publisher import recovery

    job = make_job(status=JobStatus.WRITING, worktree_path="/tmp/wt")
    tmp_queue_path.write_text(job.model_dump_json() + "\n")

    recovery.recover_in_flight(tmp_queue_path)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.QUEUED


def test_recover_in_flight_scoring_to_queued(tmp_queue_path: Path, make_job) -> None:
    job = make_job(status=JobStatus.SCORING, worktree_path="/tmp/wt")
    tmp_queue_path.write_text(job.model_dump_json() + "\n")

    from content_publisher import recovery

    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.QUEUED


def test_recover_in_flight_preview_sent_untouched(
    tmp_queue_path: Path, make_job
) -> None:
    """``preview-sent`` is left alone — deadline-checker decides at the next tick."""
    from content_publisher import recovery

    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        preview_pending=True,
        approval_deadline=datetime.now(UTC),
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n")
    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.PREVIEW_SENT


def test_recover_in_flight_attest_pending_untouched(
    tmp_queue_path: Path, make_job
) -> None:
    job = make_job(
        status=JobStatus.ATTEST_PENDING,
        attest_pending_since=datetime.now(UTC),
        post_url="https://t.me/c/1/2",
        content_sha256="a" * 64,
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n")

    from content_publisher import recovery

    recovery.recover_in_flight(tmp_queue_path)
    assert queue.load(tmp_queue_path)[0].status == JobStatus.ATTEST_PENDING


def test_recover_in_flight_terminal_untouched(tmp_queue_path: Path, make_job) -> None:
    """Terminal statuses are not touched by recovery — cleanup-gc owns them."""
    from content_publisher import recovery

    terminal = [
        JobStatus.DONE,
        JobStatus.REJECTED,
        JobStatus.REGENERATED,
        JobStatus.PUBLISH_FAILED,
        JobStatus.ATTEST_FAILED,
        JobStatus.FAILED,
    ]
    lines = []
    for t in terminal:
        j = make_job(status=t)
        lines.append(j.model_dump_json())
    tmp_queue_path.write_text("\n".join(lines) + "\n")

    recovery.recover_in_flight(tmp_queue_path)
    statuses = {j.status for j in queue.load(tmp_queue_path)}
    assert statuses == set(terminal)
