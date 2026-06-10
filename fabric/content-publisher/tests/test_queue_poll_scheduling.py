from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_publisher import main, queue

pytestmark = pytest.mark.asyncio


async def test_queue_poll_skips_future_fire_at(
    tmp_queue_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    job = queue.append_job(
        tmp_queue_path,
        prompt="future brief",
        mode="approval",
        chat_id=1,
        thread_id=2,
        reply_to_message_id=3,
        fire_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def fake_run_writing_to_preview(_job: object) -> None:
        called.append("worker")

    monkeypatch.setattr(
        "content_publisher.worker.run_writing_to_preview",
        fake_run_writing_to_preview,
    )

    await main.queue_poll_once(tmp_queue_path)

    assert called == []
    assert queue.load(tmp_queue_path)[0].id == job.id


async def test_queue_poll_processes_due_fire_at(
    tmp_queue_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    job = queue.append_job(
        tmp_queue_path,
        prompt="due brief",
        mode="approval",
        chat_id=1,
        thread_id=2,
        reply_to_message_id=3,
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    async def fake_run_writing_to_preview(seen_job: object) -> None:
        called.append(seen_job.id)  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "content_publisher.worker.run_writing_to_preview",
        fake_run_writing_to_preview,
    )

    await main.queue_poll_once(tmp_queue_path)

    assert called == [job.id]
