from __future__ import annotations

import pytest

from content_publisher.models import JobStatus
from content_publisher.queue import (
    StaleStateError,
    append_job,
    cas_status,
    load,
    regenerate_job,
    update_job_fields,
)


def _preview_sent_job(queue_path):
    job = append_job(
        queue_path,
        prompt="write about Mnemonik",
        mode="approval",
        chat_id=10,
        thread_id=20,
        reply_to_message_id=30,
    )
    cas_status(queue_path, job.id, expected=JobStatus.QUEUED, target=JobStatus.WRITING)
    cas_status(queue_path, job.id, expected=JobStatus.WRITING, target=JobStatus.SCORING)
    return cas_status(
        queue_path,
        job.id,
        expected=JobStatus.SCORING,
        target=JobStatus.PREVIEW_SENT,
        updates={"issues": ["too generic", "weak hook"]},
    )


def test_regenerate_job_links_old_and_new_in_one_write(tmp_queue_path) -> None:
    old = _preview_sent_job(tmp_queue_path)

    regenerated, new = regenerate_job(
        tmp_queue_path,
        old.id,
        feedback_for_retry="\n".join(old.issues),
    )

    assert regenerated.status == JobStatus.REGENERATED
    assert regenerated.regenerated_to == new.id
    assert new.status == JobStatus.QUEUED
    assert new.retries_of == old.id
    assert new.prompt == old.prompt
    assert new.feedback_for_retry == "too generic\nweak hook"
    assert new.chat_id == old.chat_id
    assert new.thread_id == old.thread_id
    assert new.reply_to_message_id == old.reply_to_message_id

    by_id = {j.id: j for j in load(tmp_queue_path)}
    assert by_id[old.id].regenerated_to == new.id
    assert by_id[new.id].retries_of == old.id


def test_regenerate_job_rejects_replay(tmp_queue_path) -> None:
    old = _preview_sent_job(tmp_queue_path)
    regenerate_job(tmp_queue_path, old.id)

    with pytest.raises(StaleStateError) as exc:
        regenerate_job(tmp_queue_path, old.id)

    assert exc.value.actual_status == JobStatus.REGENERATED


def test_update_job_fields_updates_preview_bookkeeping_without_status_change(
    tmp_queue_path,
) -> None:
    old = _preview_sent_job(tmp_queue_path)

    updated = update_job_fields(
        tmp_queue_path,
        old.id,
        expected_status=JobStatus.PREVIEW_SENT,
        updates={"preview_message_id": 1234, "preview_pending": False},
    )

    assert updated.status == JobStatus.PREVIEW_SENT
    assert updated.preview_message_id == 1234
    assert updated.preview_pending is False


def test_update_job_fields_rejects_status_mutation(tmp_queue_path) -> None:
    old = _preview_sent_job(tmp_queue_path)

    with pytest.raises(ValueError, match="must not update status"):
        update_job_fields(
            tmp_queue_path,
            old.id,
            updates={"status": JobStatus.REJECTED},
        )
