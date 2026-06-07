"""Schema-shape contract tests for Job.

These tests are intentionally noisy: any future PR that drops a field used by
Tasks 5/6 (publish/attest sub-loops) will turn red here before it can break
the worker.
"""

from __future__ import annotations

from datetime import datetime

from content_publisher.models import Job

EXPECTED_FIELDS = {
    "id",
    "created_at",
    "fire_at",
    "approval_deadline",
    "cleanup_at",
    "prompt",
    "feedback_for_retry",
    "mode",
    "status",
    "score",
    "issues",
    "worktree_path",
    "article_path",
    "preview_segments",
    "preview_message_id",
    "preview_pending",
    "notify_pending",
    "chat_id",
    "thread_id",
    "reply_to_message_id",
    "tentative_publish_started_at",
    "publish_error",
    "post_url",
    "post_message_ids",
    "published_at",
    "attest_pending_since",
    "error_message",
    "content_sha256",
    "attestation_hash",
    "attest_attempts",
    "regenerated_to",
    "retries_of",
}


def test_model_has_all_required_fields() -> None:
    missing = EXPECTED_FIELDS - set(Job.model_fields)
    assert not missing, f"Job missing fields required by tech-spec: {missing}"


def test_new_timestamp_fields_are_optional_datetime(make_job) -> None:
    j = make_job()
    assert j.published_at is None
    assert j.attest_pending_since is None

    # accept both ISO string and datetime object (pydantic coercion)
    j2 = make_job(published_at="2026-06-06T14:00:00Z")
    assert isinstance(j2.published_at, datetime)


def test_error_message_is_optional_str(make_job) -> None:
    j = make_job()
    assert j.error_message is None

    multiline = "Traceback:\n  File 'x.py'\n    raise RuntimeError"
    j2 = make_job(error_message=multiline)
    assert j2.error_message == multiline
