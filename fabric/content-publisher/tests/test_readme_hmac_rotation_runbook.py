"""Static check: the content-publisher ansible role README ships the HMAC
rotation runbook with the two-secret comma-separated format (AC-T11, F-004)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
README_PATH = REPO_ROOT / "infrastructure" / "ansible" / "roles" / "content-publisher" / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README_PATH.is_file(), f"content-publisher role README missing at {README_PATH}"
    return README_PATH.read_text(encoding="utf-8")


def test_readme_has_hmac_rotation_runbook_heading(readme_text: str) -> None:
    assert "## Runbook: rotate HMAC callback secret" in readme_text, (
        "HMAC rotation runbook heading missing from content-publisher role README; "
        "a rewrite that drops the section must be caught at test time."
    )


def test_readme_documents_two_secret_rotation_format(readme_text: str) -> None:
    assert "CALLBACK_HMAC_SECRETS" in readme_text, (
        "CALLBACK_HMAC_SECRETS env var name missing from README"
    )
    assert "<new>,<old>" in readme_text or "<new>,<current>" in readme_text, (
        "Two-secret comma-separated rotation format example missing from README"
    )
