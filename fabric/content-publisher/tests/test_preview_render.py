"""Decision 4: preview-byte-equality with the publish path.

The contract: ``render_preview(article_path)`` returns the exact list of strings
that ``run_campaign_from_article`` would send to Telegram. Equality is checked
BY BYTES — operator must see the same text in the preview as in the published
post.

Local-snapshot caveat
---------------------
The pinned upstream of ``mnemonik_blogger`` (set in sops as
``blogger_repo_ref``) is required to expose:

  * ``mnemonik_blogger.content.ingest.ingest_article`` — turns ``article.md``
    into a ``SourcePost``;
  * ``mnemonik_blogger.agent.run_campaign_from_article`` — the publish entry
    point whose internal Telegram sender we monkey-patch in this test;
  * ``mnemonik_blogger.publish.telegram.TelegramPublisher.publish`` — the
    confirmed seam (file ``publish/telegram.py``, class
    ``TelegramPublisher.publish``) that receives the ``RenderedPost`` whose
    ``.segments`` we compare against the preview output.

If the upstream snapshot installed in the venv does not yet ship those names
(Decision 11's ``inspect.signature`` install-time assertion is the fail-loud
gate), the byte-equality test is skipped and a regression-guard import test
still runs. The skip is loud — `pytest.importorskip` records the missing name
in the test summary so the deploy gate cannot pass silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Always-on guard: render.py must use the canonical imports.
from content_publisher.render import render_preview

FIXTURE = Path(__file__).parent / "fixtures" / "short_article.md"


def test_render_uses_config_platform_import() -> None:
    """``Platform`` is imported from ``mnemonik_blogger.config`` — not ``.content.voice``.

    Round-3 of tech-spec investigation found a regression risk: an older draft
    of upstream re-exported ``Platform`` through ``mnemonik_blogger.content.voice``.
    Importing from there is a footgun — the value space could drift. Lock the
    canonical path with an AST grep.
    """
    src = Path(render_preview.__code__.co_filename).read_text()
    assert "from mnemonik_blogger.config import Platform" in src
    assert "from .content.voice" not in src
    assert "from mnemonik_blogger.content.voice" not in src


def test_render_uses_documented_ingest_and_dispatcher_imports() -> None:
    """``ingest_article`` and ``render`` come from the documented paths."""
    src = Path(render_preview.__code__.co_filename).read_text()
    assert "from mnemonik_blogger.content.ingest import ingest_article" in src
    assert "from mnemonik_blogger.content.formatters import render" in src


def test_preview_segments_byte_equal_to_publish_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BYTE-equal: ``render_preview(p)`` == ``run_campaign_from_article(...)`` segments.

    Mechanism: monkey-patch the Telegram publisher's ``publish`` method to
    capture the ``RenderedPost`` it would have sent; then compare its
    ``.segments`` to the preview output. Verified seam (file:class:method):
    ``mnemonik_blogger/publish/telegram.py``::``TelegramPublisher.publish``.
    """
    # All three are guarded by ``importorskip`` so a not-yet-merged upstream
    # snapshot is loud — the test does not silently pass.
    pytest.importorskip(
        "mnemonik_blogger.content.ingest",
        reason="upstream snapshot missing ingest_article — Decision 11 gate",
    )
    agent = pytest.importorskip(
        "mnemonik_blogger.agent",
        reason="upstream snapshot loaded but agent module missing",
    )
    if not hasattr(agent, "run_campaign_from_article"):
        pytest.skip(
            "mnemonik_blogger.agent.run_campaign_from_article not present "
            "in this upstream snapshot — Decision 11 deploy-gate territory"
        )

    import inspect

    from mnemonik_blogger.config import Platform, Settings
    from mnemonik_blogger.content.formatters import render
    from mnemonik_blogger.content.ingest import ingest_article  # noqa: F401
    from mnemonik_blogger.publish import telegram as telegram_mod

    captured: dict[str, object] = {}

    def fake_publish(self: object, post: object) -> object:
        captured["post"] = post
        # Return a synthetic PublishResult so the agent does not crash.
        from mnemonik_blogger.models import PublishResult

        return PublishResult(Platform.TELEGRAM, ok=True, ids=["1"], urls=["https://t.me/x/1"])

    monkeypatch.setattr(telegram_mod.TelegramPublisher, "publish", fake_publish)

    # Compute the expected preview directly from the same dispatcher.
    expected = render(Platform.TELEGRAM, ingest_article(FIXTURE)).segments

    # Build Settings adaptively: as the pinned upstream evolves it may add
    # required fields (min_score, claude_blog_path, telegram, ...). Inspect
    # the live signature and supply test values for each parameter without a
    # default — otherwise this test would fail on ValidationError instead of
    # on the byte comparison and the skip would mislead.
    settings_fields: dict[str, object] = {"dry_run": False}
    try:
        sig = inspect.signature(Settings)
        for name, param in sig.parameters.items():
            if name in settings_fields:
                continue
            if param.default is not inspect.Parameter.empty:
                continue
            if name == "min_score":
                settings_fields[name] = 0
            elif name == "claude_blog_path":
                settings_fields[name] = str(FIXTURE.parent)
            elif name == "telegram":
                from mnemonik_blogger.config import TelegramConfig

                settings_fields[name] = TelegramConfig()
            else:
                settings_fields[name] = None
    except (TypeError, ValueError):
        pass
    settings = Settings(**settings_fields)

    agent.run_campaign_from_article(
        article=FIXTURE,
        platforms=[Platform.TELEGRAM],
        settings=settings,
        attest=False,
    )

    captured_segments = captured["post"].segments  # type: ignore[attr-defined]
    preview_segments = render_preview(FIXTURE)

    assert preview_segments == expected
    assert preview_segments == captured_segments
