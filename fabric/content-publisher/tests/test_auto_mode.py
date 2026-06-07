"""AC11: PUBLISH_MODE=auto skips preview and goes queued → publishing directly.

The worker step that owns this is the writing→scoring step (Task 5 — ``worker.py``)
plus the publish step (Task 6 — ``publish.py``). In auto-mode the CAS chain is:

    queued → writing → scoring → publishing → published → ... → done

`preview-sent` MUST NOT appear in the trail.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_auto_mode_skips_preview_and_publishes_directly(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """auto-mode + matching token: the worker pipeline never enters preview-sent."""
    from content_publisher import publish, queue, worker
    from content_publisher.models import JobStatus

    monkeypatch.setattr(worker, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(worker, "WORKTREE_ROOT", tmp_path)
    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 1_000_000)
    monkeypatch.setenv("PUBLISH_MODE", "auto")

    # Seed an auto-mode job.
    job = queue.append_job(
        tmp_queue_path,
        prompt="hello",
        mode="auto",
        chat_id=1,
        thread_id=2,
        reply_to_message_id=3,
    )

    article = tmp_path / job.id / "article.md"
    article.parent.mkdir(parents=True, exist_ok=True)
    article.write_text("body bytes\n", encoding="utf-8")

    with (
        patch(
            "content_publisher.worker.spawn.spawn_claude",
            new_callable=AsyncMock,
            return_value=article,
        ),
        patch(
            "content_publisher.worker.score.analyze",
            new_callable=AsyncMock,
            return_value=worker.score.ScoreResult(score=92, issues=[]),
        ),
        patch(
            "content_publisher.worker.render_preview",
            return_value=["seg1"],
        ),
    ):
        await worker.run_writing_to_preview(job)

    # In auto-mode, the worker's terminal CAS must be scoring→publishing
    # (not scoring→preview-sent). The publish step then carries it forward.
    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHING, (
        f"auto-mode must skip preview-sent; got {refreshed.status}"
    )

    # Now drive the publish step itself with a mocked blogger call.
    class PR:
        ok = True
        primary_url = "https://t.me/c/1/2"
        urls = ["https://t.me/c/1/2"]
        ids = [2]
        error = None

    class CR:
        results = [PR()]

    with patch.object(publish, "_run_campaign_from_article", return_value=CR()):
        await publish.publish_step(job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHED
    assert refreshed.post_url == "https://t.me/c/1/2"
    # T6-2: publishing→published does NOT pre-fire notify; the bot's
    # post-fact "Auto-published: <link>. Receipt: <hash>. Score: <N>."
    # message requires the receipt, so notify_pending only flips on the
    # attestation terminal.
    assert refreshed.notify_pending is False

    # Drive attestation to terminal — at DONE the bot picks up notify_pending=True
    # and sends the AC11 auto-published post-fact message.
    from content_publisher import attest

    with patch(
        "content_publisher.attest.sign_memory_via_mcp",
        new_callable=AsyncMock,
        return_value="deadbeef" * 8,
    ):
        await attest.attest_once(queue_path=tmp_queue_path, job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.DONE
    assert refreshed.attestation_hash == "deadbeef" * 8
    # AC11: bot needs notify_pending=True on the terminal attest result so it
    # can post "Auto-published: <link>. Receipt: <hash>. Score: <N>." in the topic.
    assert refreshed.notify_pending is True
