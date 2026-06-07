"""Prefix resolver — bot's callback_data is 64-byte capped so we store the
first 12 hex chars of the UUID and resolve back here.

Fail-loud on prefix collision: silently picking the first match could publish
the wrong operator's content from a misrouted callback.
"""

from __future__ import annotations

import pytest

from content_publisher.models import JobStatus
from content_publisher.queue import append_job, find_by_prefix


def test_exact_12char_match_returns_job(tmp_queue_path) -> None:
    j = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    found = find_by_prefix(tmp_queue_path, j.id[:12])
    assert found is not None
    assert found.id == j.id


def test_prefix_not_found_returns_none(tmp_queue_path) -> None:
    append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    # Well-formed 12-char prefix (8 hex + '-' + 3 hex) that does not match
    # the seeded job's id; UUID v4 birthday-collision is negligible.
    result = find_by_prefix(tmp_queue_path, "deadbeef-000")
    assert result is None


def test_ambiguous_prefix_raises_value_error(tmp_queue_path, make_job) -> None:
    # Hand-write two Jobs with hand-crafted ids sharing the first 12 hex chars.
    # Both must be valid UUID v4 — we vary only positions >= 13.
    shared = "abcd1234-12ab"
    ids = [
        f"{shared}-4def-89ab-000000000001",
        f"{shared}-4def-89ab-000000000002",
    ]
    for i in ids:
        j = make_job(id=i, status=JobStatus.QUEUED)
        # use the queue.append path to make sure file format matches reality
        with tmp_queue_path.open("a") as fh:
            fh.write(j.model_dump_json())
            fh.write("\n")

    with pytest.raises(ValueError, match="ambiguous prefix: 2 matches"):
        find_by_prefix(tmp_queue_path, ids[0][:12])


def test_invalid_prefix_length_raises(tmp_queue_path) -> None:
    with pytest.raises(ValueError):
        find_by_prefix(tmp_queue_path, "abc")
    with pytest.raises(ValueError):
        find_by_prefix(tmp_queue_path, "a" * 13)


def test_invalid_prefix_chars_raises(tmp_queue_path) -> None:
    with pytest.raises(ValueError):
        find_by_prefix(tmp_queue_path, "XYZ123456789")  # uppercase
    with pytest.raises(ValueError):
        find_by_prefix(tmp_queue_path, "ghijklmnopqr")  # not hex
