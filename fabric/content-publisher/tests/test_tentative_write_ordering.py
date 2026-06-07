"""AC-T7: ``tentative_publish_started_at`` is written to queue.jsonl BEFORE the
in-process call to ``run_campaign_from_article``.

Why this exists: in the rare double-post case (worker crashes between the
Telegram API success and our CAS publishing -> published), the operator needs
a wall-clock hint of "the recovered duplicate was published around <T>".
That hint is useless unless the timestamp is durably persisted *before* the
call, not as part of the success path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from content_publisher import queue
from content_publisher.models import JobStatus

pytestmark = pytest.mark.asyncio


async def test_tentative_publish_started_at_written_before_call(
    tmp_queue_path: Path, make_job, monkeypatch
) -> None:
    """If the in-process publish call raises, the queue line must already carry
    ``tentative_publish_started_at`` — proving the write happened FIRST.
    """
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 1_000_000)

    article = tmp_queue_path.parent / "article.md"
    article.write_text("# Hello\n\nbody\n", encoding="utf-8")

    job = make_job(
        status=JobStatus.PUBLISHING,
        article_path=str(article),
        score=85,
        approval_deadline=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    queue.append_job(
        tmp_queue_path,
        prompt=job.prompt,
        mode="approval",
        chat_id=None,
        thread_id=None,
        reply_to_message_id=None,
    )
    # Replace the queued row with a row in PUBLISHING for this test
    raw = tmp_queue_path.read_text()
    appended = queue.load(tmp_queue_path)[0]
    appended_dict = appended.model_dump(mode="json")
    appended_dict.update(
        {
            "id": job.id,
            "status": JobStatus.PUBLISHING.value,
            "article_path": str(article),
            "score": 85,
        }
    )
    from content_publisher.models import Job

    rewritten = Job.model_validate(appended_dict).model_dump_json() + "\n"
    tmp_queue_path.write_text(rewritten)
    del raw

    def boom(**_kwargs):  # noqa: ANN003
        raise RuntimeError("simulated publish failure mid-call")

    with patch.object(publish, "_run_campaign_from_article", side_effect=boom):
        with pytest.raises(RuntimeError, match="simulated"):
            await publish.publish_step(job_id=job.id)

    after = queue.load(tmp_queue_path)
    assert len(after) == 1
    refreshed = after[0]
    assert refreshed.tentative_publish_started_at is not None, (
        "tentative_publish_started_at must be set before the publish call "
        "to give the operator a duplicate-detection timestamp"
    )
