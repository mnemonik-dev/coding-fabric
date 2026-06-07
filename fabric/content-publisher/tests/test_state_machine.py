"""State machine — every valid transition allowed, every illegal one rejected.

Also pins the cas_status side-effects on the three "decorated" transitions:
    publishing -> published        sets published_at
    published  -> attest-pending   sets attest_pending_since
    *          -> failed/...       carries caller-supplied error_message
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from content_publisher.models import Job, JobStatus
from content_publisher.queue import (
    IllegalTransitionError,
    append_job,
    cas_status,
    load,
)

# ---------- pure graph tests ----------


VALID_TRANSITIONS: list[tuple[JobStatus, JobStatus]] = [
    (JobStatus.QUEUED, JobStatus.WRITING),
    (JobStatus.WRITING, JobStatus.SCORING),
    (JobStatus.WRITING, JobStatus.FAILED),
    (JobStatus.SCORING, JobStatus.PREVIEW_SENT),
    (JobStatus.SCORING, JobStatus.FAILED),
    (JobStatus.PREVIEW_SENT, JobStatus.PUBLISHING),
    (JobStatus.PREVIEW_SENT, JobStatus.REJECTED),
    (JobStatus.PREVIEW_SENT, JobStatus.REGENERATED),
    (JobStatus.PUBLISHING, JobStatus.PUBLISHED),
    (JobStatus.PUBLISHING, JobStatus.PUBLISH_FAILED),
    (JobStatus.PUBLISHED, JobStatus.ATTEST_PENDING),
    (JobStatus.ATTEST_PENDING, JobStatus.DONE),
    (JobStatus.ATTEST_PENDING, JobStatus.ATTEST_FAILED),
    (JobStatus.QUEUED, JobStatus.FAILED),
]

ILLEGAL_TRANSITIONS: list[tuple[JobStatus, JobStatus]] = [
    (JobStatus.QUEUED, JobStatus.PUBLISHED),
    (JobStatus.DONE, JobStatus.WRITING),
    (JobStatus.REJECTED, JobStatus.WRITING),
    (JobStatus.REGENERATED, JobStatus.WRITING),
    (JobStatus.PUBLISH_FAILED, JobStatus.PUBLISHING),
    (JobStatus.ATTEST_FAILED, JobStatus.ATTEST_PENDING),
    (JobStatus.FAILED, JobStatus.QUEUED),
    (JobStatus.QUEUED, JobStatus.SCORING),
]

TERMINAL_STATES = {
    JobStatus.DONE,
    JobStatus.REJECTED,
    JobStatus.REGENERATED,
    JobStatus.PUBLISH_FAILED,
    JobStatus.ATTEST_FAILED,
    JobStatus.FAILED,
}


def test_all_valid_transitions_allowed() -> None:
    table = Job.allowed_transitions()
    for src, dst in VALID_TRANSITIONS:
        assert dst in table[src], f"{src.value} -> {dst.value} should be allowed"


def test_illegal_transitions_raise(tmp_queue_path) -> None:
    """Every illegal transition is rejected by ``allowed_transitions``.

    Asserts the static map — the source of truth consulted by ``cas_status``.
    One end-to-end illegal-CAS case is covered below to pin the
    ``cas_status -> IllegalTransitionError`` wiring.
    """
    table = Job.allowed_transitions()
    for src, dst in ILLEGAL_TRANSITIONS:
        assert dst not in table.get(src, set()), (
            f"{src.value} -> {dst.value} should NOT be allowed"
        )


def test_cas_status_raises_on_illegal_transition(tmp_queue_path) -> None:
    """End-to-end: cas_status surfaces IllegalTransitionError for a forbidden edge."""
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    with pytest.raises(IllegalTransitionError):
        cas_status(
            tmp_queue_path,
            job.id,
            expected=JobStatus.QUEUED,
            target=JobStatus.PUBLISHED,
        )


def test_terminal_states_have_no_outgoing() -> None:
    table = Job.allowed_transitions()
    for term in TERMINAL_STATES:
        assert table.get(term, set()) == set(), (
            f"terminal state {term.value} should have no outgoing transitions"
        )


# ---------- side-effect tests on cas_status ----------


def _seed_at(tmp_queue_path, target_status: JobStatus) -> str:
    """Append a queued job and walk it to target_status via legal transitions."""
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    walk = {
        JobStatus.WRITING: [JobStatus.WRITING],
        JobStatus.SCORING: [JobStatus.WRITING, JobStatus.SCORING],
        JobStatus.PREVIEW_SENT: [
            JobStatus.WRITING,
            JobStatus.SCORING,
            JobStatus.PREVIEW_SENT,
        ],
        JobStatus.PUBLISHING: [
            JobStatus.WRITING,
            JobStatus.SCORING,
            JobStatus.PREVIEW_SENT,
            JobStatus.PUBLISHING,
        ],
        JobStatus.PUBLISHED: [
            JobStatus.WRITING,
            JobStatus.SCORING,
            JobStatus.PREVIEW_SENT,
            JobStatus.PUBLISHING,
            JobStatus.PUBLISHED,
        ],
    }[target_status]
    current = JobStatus.QUEUED
    for nxt in walk:
        cas_status(tmp_queue_path, job.id, expected=current, target=nxt)
        current = nxt
    return job.id


def test_transition_to_published_sets_published_at(tmp_queue_path) -> None:
    job_id = _seed_at(tmp_queue_path, JobStatus.PUBLISHING)

    # Pre-state: published_at must still be None.
    [pre] = [j for j in load(tmp_queue_path) if j.id == job_id]
    assert pre.published_at is None

    before = datetime.now(UTC)
    updated = cas_status(
        tmp_queue_path,
        job_id,
        expected=JobStatus.PUBLISHING,
        target=JobStatus.PUBLISHED,
    )
    after = datetime.now(UTC)
    assert updated.published_at is not None
    assert before <= updated.published_at <= after


def test_transition_to_attest_pending_sets_attest_pending_since(tmp_queue_path) -> None:
    job_id = _seed_at(tmp_queue_path, JobStatus.PUBLISHED)

    [pre] = [j for j in load(tmp_queue_path) if j.id == job_id]
    assert pre.attest_pending_since is None

    before = datetime.now(UTC)
    updated = cas_status(
        tmp_queue_path,
        job_id,
        expected=JobStatus.PUBLISHED,
        target=JobStatus.ATTEST_PENDING,
    )
    after = datetime.now(UTC)
    assert updated.attest_pending_since is not None
    assert before <= updated.attest_pending_since <= after


def test_transition_to_terminal_error_records_error_message(tmp_queue_path) -> None:
    # writing -> failed
    job_id = _seed_at(tmp_queue_path, JobStatus.WRITING)
    updated = cas_status(
        tmp_queue_path,
        job_id,
        expected=JobStatus.WRITING,
        target=JobStatus.FAILED,
        updates={"error_message": "claude crashed: exit 137"},
    )
    assert updated.error_message == "claude crashed: exit 137"

    # publishing -> publish-failed
    job_id2 = _seed_at(tmp_queue_path, JobStatus.PUBLISHING)
    updated2 = cas_status(
        tmp_queue_path,
        job_id2,
        expected=JobStatus.PUBLISHING,
        target=JobStatus.PUBLISH_FAILED,
        updates={"error_message": "telegram 429"},
    )
    assert updated2.error_message == "telegram 429"

    # attest-pending -> attest-failed (must first walk to attest-pending)
    job_id3 = _seed_at(tmp_queue_path, JobStatus.PUBLISHED)
    cas_status(
        tmp_queue_path,
        job_id3,
        expected=JobStatus.PUBLISHED,
        target=JobStatus.ATTEST_PENDING,
    )
    updated3 = cas_status(
        tmp_queue_path,
        job_id3,
        expected=JobStatus.ATTEST_PENDING,
        target=JobStatus.ATTEST_FAILED,
        updates={"error_message": "24h elapsed"},
    )
    assert updated3.error_message == "24h elapsed"
