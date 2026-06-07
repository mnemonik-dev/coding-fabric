"""publish-step contract tests (AC b-e + l)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from content_publisher import queue
from content_publisher.models import JobStatus

pytestmark = pytest.mark.asyncio


class _PR:
    def __init__(self, *, ok=True, primary_url=None, urls=None, ids=None, error=None):
        self.ok = ok
        self.primary_url = primary_url
        self.urls = urls or []
        self.ids = ids or []
        self.error = error


class _CR:
    def __init__(self, results):
        self.results = results


def _seed_publishing(tmp_queue_path: Path, article: Path, make_job, **extra):
    job = make_job(
        status=JobStatus.PUBLISHING,
        article_path=str(article),
        score=90,
        **extra,
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")
    return job


async def test_publish_success_transitions_to_published(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 100)

    article = tmp_path / "article.md"
    article.write_text("hello world\n", encoding="utf-8")

    job = _seed_publishing(tmp_queue_path, article, make_job)

    pr = _PR(ok=True, primary_url="https://t.me/c/1/2", urls=["..."], ids=[2, 3])
    with patch.object(publish, "_run_campaign_from_article", return_value=_CR([pr])):
        await publish.publish_step(job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHED
    assert refreshed.post_url == "https://t.me/c/1/2"
    assert refreshed.post_message_ids == [2, 3]
    assert refreshed.content_sha256 is not None
    assert len(refreshed.content_sha256) == 64
    assert refreshed.published_at is not None
    # User-spec step 9: the "Опубликовано. Receipt: <hash>." consolidated
    # message can only be assembled after attestation. notify_pending MUST
    # NOT be set on publishing→published — attest.py handles it on terminal.
    assert refreshed.notify_pending is False


async def test_publish_failure_transitions_to_publish_failed(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 100)

    article = tmp_path / "article.md"
    article.write_text("hello\n", encoding="utf-8")
    job = _seed_publishing(tmp_queue_path, article, make_job)

    pr = _PR(ok=False, error="429 Too Many Requests")
    with patch.object(publish, "_run_campaign_from_article", return_value=_CR([pr])):
        await publish.publish_step(job_id=job.id)

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISH_FAILED
    assert refreshed.publish_error == "429 Too Many Requests"
    assert refreshed.notify_pending is True


async def test_publish_uses_keyword_only_signature(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """Verify the in-process call site uses ONLY keyword args, NO dry_run on the
    function (it's on Settings).
    """
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 100)

    article = tmp_path / "article.md"
    article.write_text("hello\n", encoding="utf-8")
    job = _seed_publishing(tmp_queue_path, article, make_job)

    pr = _PR(ok=True, primary_url="https://t.me/c/1/2", ids=[2])
    captured = {}

    def capture(*args, **kwargs):  # noqa: ANN002, ANN003
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _CR([pr])

    with patch.object(publish, "_run_campaign_from_article", side_effect=capture):
        await publish.publish_step(job_id=job.id)

    # Strictly keyword-only.
    assert captured["args"] == (), f"positional args leaked: {captured['args']!r}"
    # No "dry_run" kwarg on the function itself.
    assert "dry_run" not in captured["kwargs"], (
        "dry_run lives on Settings, not as a keyword argument to "
        "run_campaign_from_article"
    )
    # Required kwargs must be present.
    for key in ("article", "platforms", "settings", "attest"):
        assert key in captured["kwargs"], f"missing required kwarg {key!r}"
    # attest=False — we attest via MCP-stdio (Decision 8).
    assert captured["kwargs"]["attest"] is False


async def test_publish_rate_ceiling_blocks_publication(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """Security R2-9: >N posts in the last 60 minutes → publish-failed BEFORE
    the in-process call.
    """
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 2)

    article = tmp_path / "article.md"
    article.write_text("hello\n", encoding="utf-8")

    # Seed 3 already-published jobs in the last hour.
    now = datetime.now(UTC)
    lines = []
    for i in range(3):
        j = make_job(
            status=JobStatus.PUBLISHED,
            article_path=str(article),
            post_url=f"https://t.me/c/1/{i}",
            content_sha256="a" * 64,
            published_at=now - timedelta(minutes=10 + i),
            score=80,
        )
        lines.append(j.model_dump_json())

    # And a fresh job in publishing.
    target = make_job(status=JobStatus.PUBLISHING, article_path=str(article), score=90)
    lines.append(target.model_dump_json())
    tmp_queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    called = {"value": False}

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        called["value"] = True
        raise AssertionError("rate ceiling did NOT block — in-process call happened")

    with patch.object(publish, "_run_campaign_from_article", side_effect=boom):
        await publish.publish_step(job_id=target.id)

    assert called["value"] is False
    refreshed = next(
        j for j in queue.load(tmp_queue_path) if j.id == target.id
    )
    assert refreshed.status == JobStatus.PUBLISH_FAILED
    assert refreshed.publish_error is not None
    assert "rate ceiling" in refreshed.publish_error.lower()
    assert refreshed.notify_pending is True


async def test_publish_rate_ceiling_ignores_old_publications(
    tmp_queue_path: Path, tmp_path: Path, make_job, monkeypatch
) -> None:
    """Old publications (>60min ago) do NOT count toward the ceiling."""
    from content_publisher import publish

    monkeypatch.setattr(publish, "QUEUE_PATH", tmp_queue_path)
    monkeypatch.setattr(publish, "POSTS_PER_HOUR_CEILING", 1)

    article = tmp_path / "article.md"
    article.write_text("hello\n", encoding="utf-8")

    now = datetime.now(UTC)
    lines = []
    # 2 hours ago → does not count.
    j = make_job(
        status=JobStatus.PUBLISHED,
        article_path=str(article),
        post_url="https://t.me/c/1/0",
        content_sha256="a" * 64,
        published_at=now - timedelta(hours=2),
        score=80,
    )
    lines.append(j.model_dump_json())

    target = make_job(status=JobStatus.PUBLISHING, article_path=str(article), score=90)
    lines.append(target.model_dump_json())
    tmp_queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pr = _PR(ok=True, primary_url="https://t.me/c/1/1", ids=[1])
    with patch.object(publish, "_run_campaign_from_article", return_value=_CR([pr])):
        await publish.publish_step(job_id=target.id)

    refreshed = next(j for j in queue.load(tmp_queue_path) if j.id == target.id)
    assert refreshed.status == JobStatus.PUBLISHED
