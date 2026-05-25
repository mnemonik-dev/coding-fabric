"""Unit tests for dispatch helpers (repo-binding parser + task_id)."""

from __future__ import annotations

import pytest

from dispatch import parse_repo_from_description, stage_for, task_id_for
from kaneo_client import Ticket


def _ticket(**overrides) -> Ticket:
    defaults = dict(
        id="x",
        title="t",
        description="",
        status="to-do",
        project_id="p",
        labels=(),
        raw={},
    )
    defaults.update(overrides)
    return Ticket(**defaults)


# ----- parse_repo_from_description -----


def test_parse_repo_org_name() -> None:
    assert parse_repo_from_description("repo: foo/bar") == "https://github.com/foo/bar.git"


def test_parse_repo_full_https_url() -> None:
    assert (
        parse_repo_from_description("repo: https://github.com/foo/bar.git")
        == "https://github.com/foo/bar.git"
    )


def test_parse_repo_ssh_url() -> None:
    assert (
        parse_repo_from_description("repo: git@github.com:foo/bar.git")
        == "git@github.com:foo/bar.git"
    )


def test_parse_repo_case_insensitive_directive() -> None:
    assert parse_repo_from_description("Repo: foo/bar") == "https://github.com/foo/bar.git"


def test_parse_repo_in_multiline_description() -> None:
    description = """
    Project for Mnemonic Core.

    repo: mnemonik-dev/mnemonic-core

    Notes...
    """
    assert (
        parse_repo_from_description(description)
        == "https://github.com/mnemonik-dev/mnemonic-core.git"
    )


def test_parse_repo_returns_none_when_absent() -> None:
    assert parse_repo_from_description("just a project description") is None
    assert parse_repo_from_description("") is None
    assert parse_repo_from_description(None) is None


def test_parse_repo_invalid_shape() -> None:
    # Not a URL, not org/name → unparseable
    assert parse_repo_from_description("repo: just-a-name") is None
    # Too many slashes → ambiguous
    assert parse_repo_from_description("repo: a/b/c") is None


# ----- stage_for -----


def test_stage_default_is_code() -> None:
    assert stage_for(_ticket()) == "code"


def test_stage_from_label() -> None:
    assert stage_for(_ticket(labels=("stage:review",))) == "review"
    assert stage_for(_ticket(labels=("urgent", "stage:qa"))) == "qa"


def test_stage_override_default() -> None:
    assert stage_for(_ticket(), default="other") == "other"


# ----- task_id_for -----


def test_task_id_sanitizes() -> None:
    assert task_id_for(_ticket(id="ba8gqr45su8nxbdjmtwcywao")) == "BA8GQR45SU8NXBDJMTWCYWAO"


def test_task_id_strips_invalid_chars() -> None:
    assert task_id_for(_ticket(id="abc.def_ghi")) == "ABC-DEF-GHI"


def test_task_id_truncates_to_40() -> None:
    long = "a" * 100
    got = task_id_for(_ticket(id=long))
    assert len(got) == 40
    assert got == "A" * 40


def test_task_id_pads_too_short() -> None:
    assert task_id_for(_ticket(id="x")) == "X--"
