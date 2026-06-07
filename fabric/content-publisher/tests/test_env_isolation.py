"""restricted_env(kind) allowlist tests.

Each subprocess kind gets ONLY the variables in the tech-spec table; nothing
else leaks (the seeded FAKE_SECRET would catch a blacklist-style bug).
"""

from __future__ import annotations

import pytest

from content_publisher.env import restricted_env


@pytest.fixture(autouse=True)
def _seed_env(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin")
    monkeypatch.setenv("HOME", "/var/lib/content-publisher")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-claude")
    monkeypatch.setenv("MNEMONIK_CLAUDE_BLOG_PATH", "/opt/claude-blog")
    monkeypatch.setenv("FAKE_SECRET", "should-not-leak")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("CALLBACK_HMAC_SECRETS", "hmac")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_claude_env_subset_matches_allowlist() -> None:
    env = restricted_env("claude")
    assert env == {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
        "HOME": "/var/lib/content-publisher",
        "CLAUDE_CODE_OAUTH_TOKEN": "tok-claude",
    }


def test_claude_env_accepts_anthropic_api_key_fallback(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fallback")
    env = restricted_env("claude")
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-fallback"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "FAKE_SECRET" not in env


def test_mnemonik_mcp_env_has_only_PATH() -> None:
    env = restricted_env("mnemonik-mcp")
    assert env == {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"}


def test_analyze_blog_env_has_PATH_and_claude_blog_path() -> None:
    env = restricted_env("analyze_blog")
    assert env == {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
        "MNEMONIK_CLAUDE_BLOG_PATH": "/opt/claude-blog",
    }


def test_claude_env_raises_if_no_oauth_or_api_key(monkeypatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        restricted_env("claude")


def test_blogger_inproc_returns_full_env() -> None:
    env = restricted_env("blogger_inproc")
    # In-process invocation: caller sees everything its parent had.
    assert env.get("TELEGRAM_BOT_TOKEN") == "tg-token"
    assert env.get("MNEMONIK_CLAUDE_BLOG_PATH") == "/opt/claude-blog"
