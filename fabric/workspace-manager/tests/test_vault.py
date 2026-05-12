"""
Unit tests for vault.py.

TDD anchors:
  - test_env_for_topic_matches_topic_secrets
  - vault unreachable → VaultUnreachableError
  - .env written with mode 0600
  - no secret values appear in log output
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vault import (
    ENV_MODE,
    VaultUnreachableError,
    _extract_env_lines,
    _load_bw_session,
    materialise_env,
    remove_env,
)


# ---------------------------------------------------------------------------
# _load_bw_session
# ---------------------------------------------------------------------------


def test_load_bw_session_reads_file(tmp_path):
    cred = tmp_path / "bw-session"
    cred.write_text("abc123\n")
    assert _load_bw_session(cred) == "abc123"


def test_load_bw_session_missing_raises(tmp_path):
    with pytest.raises(VaultUnreachableError):
        _load_bw_session(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# _extract_env_lines
# ---------------------------------------------------------------------------


def test_extract_env_lines_parses_notes():
    item = {"notes": "KEY1=val1\nKEY2=val2\n# comment\n\n"}
    lines = _extract_env_lines(item, "docs")
    assert "KEY1=val1" in lines
    assert "KEY2=val2" in lines
    # Comment lines must not appear
    assert not any(ln.startswith("#") for ln in lines)


def test_extract_env_lines_injects_sccache():
    item = {"notes": "SOME_KEY=value"}
    lines = _extract_env_lines(item, "docs")
    sccache_lines = [ln for ln in lines if ln.startswith("SCCACHE_DIR=")]
    assert len(sccache_lines) == 1


def test_extract_env_lines_no_sccache_duplicate():
    item = {"notes": "SCCACHE_DIR=/custom/path\nOTHER=val"}
    lines = _extract_env_lines(item, "docs")
    sccache_lines = [ln for ln in lines if ln.startswith("SCCACHE_DIR=")]
    assert len(sccache_lines) == 1


# ---------------------------------------------------------------------------
# test_env_for_topic_matches_topic_secrets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topic,expected_keys", [
    ("docs", ["BW_ITEM", "ANTHROPIC_API_KEY"]),
    ("core", ["BW_ITEM", "OPENAI_API_KEY"]),
])
def test_env_for_topic_matches_topic_secrets(tmp_path, topic, expected_keys):
    """
    The .env written for a topic contains exactly the keys defined for that topic.
    """
    from tests.conftest import make_bw_item, FAKE_TOPIC_SECRETS

    credential = tmp_path / "bw-session"
    credential.write_text("dummy-session\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    bw_item = make_bw_item(topic)

    with (
        patch("vault._load_bw_session", return_value="dummy-session"),
        patch("vault._bw_get_item", return_value=bw_item),
    ):
        env_path = materialise_env(
            task_id="T-001",
            topic=topic,
            worktree_path=wt_path,
            credential_path=credential,
        )

    assert env_path.exists()
    content = env_path.read_text()
    for key in expected_keys:
        assert f"{key}=" in content, f"Missing key {key} in .env for topic {topic}"


# ---------------------------------------------------------------------------
# .env file mode 0600
# ---------------------------------------------------------------------------


def test_env_file_mode_600(tmp_path):
    credential = tmp_path / "bw-session"
    credential.write_text("dummy-session\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    item = {"notes": "SECRET_KEY=secret-value"}

    with (
        patch("vault._load_bw_session", return_value="dummy-session"),
        patch("vault._bw_get_item", return_value=item),
    ):
        env_path = materialise_env("T-001", "docs", wt_path, credential)

    stat = env_path.stat()
    assert oct(stat.st_mode)[-3:] == "600", f"Expected 0600, got {oct(stat.st_mode)}"


# ---------------------------------------------------------------------------
# Vault unreachable
# ---------------------------------------------------------------------------


def test_vault_unreachable_bw_not_found(tmp_path):
    credential = tmp_path / "bw-session"
    credential.write_text("dummy-session\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    with (
        patch("vault._load_bw_session", return_value="dummy-session"),
        patch("vault._bw_get_item", side_effect=VaultUnreachableError("bw not found")),
    ):
        with pytest.raises(VaultUnreachableError):
            materialise_env("T-001", "docs", wt_path, credential)


# ---------------------------------------------------------------------------
# No secret values in log output
# ---------------------------------------------------------------------------


def test_vault_does_not_log_secrets(tmp_path, caplog):
    """
    materialise_env must not emit raw secret values in log records.
    Only structured keys (topic, key_count) should appear.
    """
    credential = tmp_path / "bw-session"
    credential.write_text("dummy-session\n")
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()

    secret_value = "super-secret-do-not-log"
    item = {"notes": f"SECRET={secret_value}"}

    with (
        patch("vault._load_bw_session", return_value="dummy-session"),
        patch("vault._bw_get_item", return_value=item),
        caplog.at_level(logging.DEBUG, logger="vault"),
    ):
        materialise_env("T-001", "docs", wt_path, credential)

    full_log = "\n".join(caplog.messages)
    assert secret_value not in full_log, f"Secret value leaked into logs: {full_log}"


# ---------------------------------------------------------------------------
# remove_env
# ---------------------------------------------------------------------------


def test_remove_env_deletes_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEY=val\n")
    remove_env(env)
    assert not env.exists()


def test_remove_env_idempotent(tmp_path):
    env = tmp_path / ".env"
    # Should not raise even if file doesn't exist
    remove_env(env)
