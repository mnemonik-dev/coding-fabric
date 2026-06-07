from __future__ import annotations

import asyncio
import sys

from content_publisher import main


def test_scrub_traceback_text_redacts_secret_values_and_payload_labels(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:secret-token")
    monkeypatch.setenv("CALLBACK_HMAC_SECRETS", "hmac-secret")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN", "auto-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-token")

    raw = (
        "article_text prompt TELEGRAM_BOT_TOKEN 123456:secret-token "
        "CALLBACK_HMAC_SECRETS hmac-secret PUBLISH_AUTO_MODE_TOKEN auto-token "
        "CLAUDE_CODE_OAUTH_TOKEN claude-token ANTHROPIC_API_KEY anthropic-token"
    )

    scrubbed = main.scrub_traceback_text(raw)

    for leaked in (
        "123456:secret-token",
        "hmac-secret",
        "auto-token",
        "claude-token",
        "anthropic-token",
        "article_text",
        "prompt",
        "TELEGRAM_BOT_TOKEN",
        "CALLBACK_HMAC_SECRETS",
        "PUBLISH_AUTO_MODE_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
    ):
        assert leaked not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_install_exception_redaction_hooks_sets_process_and_loop_handlers() -> None:
    original_hook = sys.excepthook
    loop = asyncio.new_event_loop()
    try:
        main.install_exception_redaction_hooks(loop)

        assert sys.excepthook is not original_hook
        assert loop.get_exception_handler() is not None
    finally:
        sys.excepthook = original_hook
        loop.close()
