"""Queue JSONL primitive tests.

Most important test in the file: test_cas_status_atomic_under_concurrent_clicks
— it models the race "operator click + deadline fire" that is THE security
property of the publish pipeline.
"""

from __future__ import annotations

import json
import re
import threading

import pytest

from content_publisher.models import JobStatus
from content_publisher.queue import (
    StaleStateError,
    append_job,
    cas_status,
    load,
)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_append_job_creates_uuid_v4_and_queued_status(tmp_queue_path) -> None:
    job = append_job(
        tmp_queue_path,
        prompt="hello",
        mode="approval",
        chat_id=42,
        thread_id=7,
        reply_to_message_id=99,
    )
    assert UUID4_RE.fullmatch(job.id), f"not a UUID v4: {job.id}"
    assert job.status == JobStatus.QUEUED
    assert job.prompt == "hello"
    assert job.mode == "approval"

    raw = tmp_queue_path.read_text().splitlines()
    assert len(raw) == 1
    parsed = json.loads(raw[0])
    assert parsed["id"] == job.id
    assert parsed["status"] == "queued"


def test_append_is_atomic_under_concurrent_writers(tmp_queue_path) -> None:
    n = 10
    barrier = threading.Barrier(n)
    ids: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        j = append_job(
            tmp_queue_path,
            prompt="p",
            mode="approval",
            chat_id=None,
            thread_id=None,
            reply_to_message_id=None,
        )
        with lock:
            ids.append(j.id)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = tmp_queue_path.read_text().splitlines()
    assert len(lines) == n
    assert len(set(ids)) == n
    # Every line is parseable JSON — no truncation, no interleaving.
    for line in lines:
        json.loads(line)


def test_load_skips_blank_and_malformed_lines(tmp_queue_path, caplog) -> None:
    # First seed one valid job so we have a known-good line.
    good = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    with tmp_queue_path.open("a") as fh:
        fh.write("\n")  # blank
        fh.write("    \n")  # whitespace only
        fh.write("{not json\n")  # malformed
        fh.write("\n")

    with caplog.at_level("WARNING"):
        jobs = load(tmp_queue_path)

    assert [j.id for j in jobs] == [good.id]
    # Exactly ONE malformed-JSON line was injected; blank/whitespace lines are
    # silently skipped (they are not errors). Asserting count = 1 catches a
    # regression where load() either swallows the warning or fires it for
    # benign blank lines.
    warning_records = [
        r
        for r in caplog.records
        if "malformed" in r.message.lower() or "skip" in r.message.lower()
    ]
    assert len(warning_records) == 1, (
        f"expected 1 warning for the single bad JSON line, got {len(warning_records)}"
    )


def test_cas_status_succeeds_when_expected_matches(tmp_queue_path) -> None:
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    updated = cas_status(
        tmp_queue_path,
        job.id,
        expected=JobStatus.QUEUED,
        target=JobStatus.WRITING,
    )
    assert updated.status == JobStatus.WRITING

    [from_disk] = [j for j in load(tmp_queue_path) if j.id == job.id]
    assert from_disk.status == JobStatus.WRITING


def test_cas_status_raises_stale_when_expected_mismatch(tmp_queue_path) -> None:
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    # advance the job past `queued`
    cas_status(tmp_queue_path, job.id, expected=JobStatus.QUEUED, target=JobStatus.WRITING)
    cas_status(tmp_queue_path, job.id, expected=JobStatus.WRITING, target=JobStatus.SCORING)

    # snapshot file before the failing CAS
    pre = tmp_queue_path.read_bytes()
    with pytest.raises(StaleStateError) as exc:
        cas_status(
            tmp_queue_path,
            job.id,
            expected=JobStatus.QUEUED,
            target=JobStatus.WRITING,
        )
    assert exc.value.actual_status == JobStatus.SCORING
    assert tmp_queue_path.read_bytes() == pre, "file mutated despite stale CAS"


def test_cas_status_atomic_under_concurrent_clicks(tmp_queue_path) -> None:
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    # walk to preview-sent
    cas_status(tmp_queue_path, job.id, expected=JobStatus.QUEUED, target=JobStatus.WRITING)
    cas_status(tmp_queue_path, job.id, expected=JobStatus.WRITING, target=JobStatus.SCORING)
    cas_status(
        tmp_queue_path,
        job.id,
        expected=JobStatus.SCORING,
        target=JobStatus.PREVIEW_SENT,
    )

    barrier = threading.Barrier(2)
    results: list[Exception | str] = []
    lock = threading.Lock()

    def clicker() -> None:
        barrier.wait()
        try:
            cas_status(
                tmp_queue_path,
                job.id,
                expected=JobStatus.PREVIEW_SENT,
                target=JobStatus.PUBLISHING,
            )
            with lock:
                results.append("ok")
        except Exception as e:  # noqa: BLE001
            with lock:
                results.append(e)

    a = threading.Thread(target=clicker)
    b = threading.Thread(target=clicker)
    a.start()
    b.start()
    a.join()
    b.join()

    oks = [r for r in results if r == "ok"]
    stales = [r for r in results if isinstance(r, StaleStateError)]
    assert len(oks) == 1, f"expected exactly one success, got {results!r}"
    assert len(stales) == 1, f"expected exactly one StaleStateError, got {results!r}"
    assert stales[0].actual_status == JobStatus.PUBLISHING


def test_cas_status_preserves_unknown_lines(tmp_queue_path) -> None:
    """Invariant: cas_status never silently deletes data it cannot parse.

    Reason: a kill-9 mid-``_write_raw`` can leave a half-written last line.
    Dropping it on the next CAS rewrite would silently lose a job. Instead we
    keep the line verbatim and let an operator inspect / repair it.
    """
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    # Append a malformed line that simulates a crash mid-write.
    with tmp_queue_path.open("a") as fh:
        fh.write('{"id": "11111111-1111-4111-8111-1111111111')  # truncated, no newline

    cas_status(
        tmp_queue_path,
        job.id,
        expected=JobStatus.QUEUED,
        target=JobStatus.WRITING,
    )

    # After rewrite, the malformed line must still be present in the file.
    contents = tmp_queue_path.read_text()
    assert '{"id": "11111111-1111-4111-8111-1111111111' in contents, (
        "cas_status silently dropped a malformed line — data-loss regression"
    )
    # And load() still surfaces the legitimate job, ignoring the bad line.
    jobs = load(tmp_queue_path)
    assert [j.id for j in jobs] == [job.id]


def test_cas_status_updates_field_atomically(tmp_queue_path) -> None:
    job = append_job(
        tmp_queue_path,
        prompt="p",
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    cas_status(tmp_queue_path, job.id, expected=JobStatus.QUEUED, target=JobStatus.WRITING)
    updated = cas_status(
        tmp_queue_path,
        job.id,
        expected=JobStatus.WRITING,
        target=JobStatus.SCORING,
        updates={"score": 85, "issues": ["weak intro"]},
    )
    assert updated.score == 85
    assert updated.issues == ["weak intro"]
    assert updated.status == JobStatus.SCORING

    [from_disk] = [j for j in load(tmp_queue_path) if j.id == job.id]
    assert from_disk.score == 85
    assert from_disk.issues == ["weak intro"]
