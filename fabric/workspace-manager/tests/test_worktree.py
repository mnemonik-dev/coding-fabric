"""
Unit tests for worktree.py.

Covers:
  - create_worktree happy path
  - destroy_worktree happy path + idempotency
  - WorktreeError on git failure
  - worktree_path deterministic calculation
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worktree import WorktreeError, create_worktree, destroy_worktree, worktree_path


# ---------------------------------------------------------------------------
# worktree_path
# ---------------------------------------------------------------------------


def test_worktree_path_deterministic(tmp_path):
    root = tmp_path / "worktrees"
    p = worktree_path("TASK-001", root, "mnemonic-docs")
    assert p == root / "TASK-001" / "mnemonic-docs"


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------


def test_create_worktree_calls_git_add(tmp_path):
    repo = tmp_path / "repos" / "mnemonic-docs"
    repo.mkdir(parents=True)
    worktrees_root = tmp_path / "worktrees"
    sccache_dir = tmp_path / "sccache"
    sccache_dir.mkdir()

    def fake_run(cmd, cwd=None):
        if cmd[2] == "add":
            Path(cmd[4]).mkdir(parents=True, exist_ok=True)
        m = MagicMock()
        m.returncode = 0
        return m

    with patch("worktree._run", side_effect=fake_run) as mock_run:
        result = create_worktree(
            task_id="TASK-001",
            repo="mnemonic-docs",
            base_ref="main",
            repos_root=tmp_path / "repos",
            worktrees_root=worktrees_root,
            sccache_dir=sccache_dir,
        )

    mock_run.assert_called_once()
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[0] == "git"
    assert called_cmd[2] == "add"
    assert "--detach" in called_cmd
    assert "main" in called_cmd
    assert result == worktrees_root / "TASK-001" / "mnemonic-docs"


def test_create_worktree_repo_not_found(tmp_path):
    with pytest.raises(WorktreeError, match="repo not found"):
        create_worktree(
            task_id="T-001",
            repo="nonexistent-repo",
            base_ref="main",
            repos_root=tmp_path / "repos",
            worktrees_root=tmp_path / "worktrees",
            sccache_dir=tmp_path / "sccache",
        )


def test_create_worktree_git_failure_raises(tmp_path):
    repo = tmp_path / "repos" / "mnemonic-docs"
    repo.mkdir(parents=True)

    def fail_run(cmd, cwd=None):
        raise WorktreeError("git returned 128")

    with patch("worktree._run", side_effect=fail_run):
        with pytest.raises(WorktreeError):
            create_worktree(
                task_id="T-001",
                repo="mnemonic-docs",
                base_ref="bad-ref",
                repos_root=tmp_path / "repos",
                worktrees_root=tmp_path / "worktrees",
                sccache_dir=tmp_path / "sccache",
            )


# ---------------------------------------------------------------------------
# destroy_worktree
# ---------------------------------------------------------------------------


def test_destroy_worktree_removes_path(tmp_path):
    repo = tmp_path / "repos" / "mnemonic-docs"
    repo.mkdir(parents=True)
    wt = tmp_path / "worktrees" / "TASK-001" / "mnemonic-docs"
    wt.mkdir(parents=True)
    (wt / "some_file.txt").write_text("data")

    def fake_run(cmd, cwd=None):
        if cmd[2] == "remove":
            shutil.rmtree(cmd[4], ignore_errors=True)
        m = MagicMock()
        m.returncode = 0
        return m

    with patch("worktree._run", side_effect=fake_run):
        destroy_worktree(
            task_id="TASK-001",
            repo="mnemonic-docs",
            repos_root=tmp_path / "repos",
            worktrees_root=tmp_path / "worktrees",
        )

    assert not wt.exists()


def test_destroy_worktree_idempotent_when_path_missing(tmp_path):
    repo = tmp_path / "repos" / "mnemonic-docs"
    repo.mkdir(parents=True)

    # No worktree directory — should not raise
    destroy_worktree(
        task_id="GHOST-001",
        repo="mnemonic-docs",
        repos_root=tmp_path / "repos",
        worktrees_root=tmp_path / "worktrees",
    )


def test_destroy_worktree_git_failure_is_best_effort(tmp_path):
    repo = tmp_path / "repos" / "mnemonic-docs"
    repo.mkdir(parents=True)
    wt = tmp_path / "worktrees" / "TASK-001" / "mnemonic-docs"
    wt.mkdir(parents=True)

    # git worktree remove fails — destroy_worktree should not raise
    with patch("worktree._run", side_effect=WorktreeError("git remove failed")):
        destroy_worktree(
            task_id="TASK-001",
            repo="mnemonic-docs",
            repos_root=tmp_path / "repos",
            worktrees_root=tmp_path / "worktrees",
        )
