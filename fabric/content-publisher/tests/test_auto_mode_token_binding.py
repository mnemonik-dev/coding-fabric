"""AC-T10: PUBLISH_MODE=auto requires two-location token match.

When ``PUBLISH_MODE=auto``:
  * ``/etc/blogger.env`` exposes ``PUBLISH_AUTO_MODE_TOKEN`` in the process env.
  * sops exposes ``publish_auto_mode_token`` (rendered into the same env file
    by Ansible per Decision 7).

Mismatch on startup → process aborts with ``SystemExit``. None of the sub-loops
must start.
"""

from __future__ import annotations

import pytest


def test_matching_tokens_pass_check(monkeypatch) -> None:
    from content_publisher import main

    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN", "same-token")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN_CONFIRM", "same-token")

    # No raise expected.
    main.assert_auto_mode_token_match_or_exit()


def test_mismatched_auto_mode_token_aborts_startup(monkeypatch) -> None:
    from content_publisher import main

    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN", "one")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN_CONFIRM", "two")

    with pytest.raises(SystemExit):
        main.assert_auto_mode_token_match_or_exit()


def test_approval_mode_ignores_tokens(monkeypatch) -> None:
    """In approval-mode, presence/absence/mismatch of the auto token is irrelevant."""
    from content_publisher import main

    monkeypatch.setenv("PUBLISH_MODE", "approval")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN", "a")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN_CONFIRM", "b")

    main.assert_auto_mode_token_match_or_exit()


def test_auto_mode_missing_token_aborts(monkeypatch) -> None:
    """auto-mode with NO token at all is also a hard failure (defense-in-depth)."""
    from content_publisher import main

    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.delenv("PUBLISH_AUTO_MODE_TOKEN", raising=False)
    monkeypatch.delenv("PUBLISH_AUTO_MODE_TOKEN_CONFIRM", raising=False)

    with pytest.raises(SystemExit):
        main.assert_auto_mode_token_match_or_exit()


def test_auto_mode_empty_string_token_aborts(monkeypatch) -> None:
    """An empty token on both sides "matches" but must still abort — empty
    means "operator forgot to set it"."""
    from content_publisher import main

    monkeypatch.setenv("PUBLISH_MODE", "auto")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN", "")
    monkeypatch.setenv("PUBLISH_AUTO_MODE_TOKEN_CONFIRM", "")

    with pytest.raises(SystemExit):
        main.assert_auto_mode_token_match_or_exit()
