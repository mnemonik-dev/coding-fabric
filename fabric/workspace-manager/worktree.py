"""
Git worktree create/destroy wrapper.

Shells out to `git worktree add --detach <path> <base_ref>` to avoid
creating a local branch.  The worktrees root is resolved from Settings;
no user-supplied path components are passed directly to the shell
(task_id is strictly validated by the API layer before reaching here).
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WorktreeError(Exception):
    pass


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    logger.debug("git cmd: %s", shlex.join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    if result.returncode != 0:
        # Log stderr via structured field; do not emit raw bw output (N/A here, but
        # keep pattern consistent for security).
        logger.error(
            "git command failed",
            extra={"cmd": shlex.join(cmd), "returncode": result.returncode},
        )
        raise WorktreeError(
            f"git returned {result.returncode}: {result.stderr.strip()}"
        )
    return result


def worktree_path(task_id: str, worktrees_root: Path, repo: str) -> Path:
    """
    Deterministic path: <worktrees_root>/<task_id>/<repo>

    task_id and repo are validated by the API layer before reaching here.
    We perform a final containment check to guarantee the resolved path cannot
    escape worktrees_root even if validation is bypassed by future code paths.
    """
    candidate = (worktrees_root / task_id / repo).resolve()
    root = worktrees_root.resolve()
    if not str(candidate).startswith(str(root) + "/") and candidate != root:
        raise WorktreeError(
            f"resolved path escapes worktrees_root: {candidate}"
        )
    return worktrees_root / task_id / repo


def create_worktree(
    task_id: str,
    repo: str,
    base_ref: str,
    repos_root: Path,
    worktrees_root: Path,
    sccache_dir: Path,
) -> Path:
    """
    Create a git worktree at <worktrees_root>/<task_id>/<repo>.

    Returns the absolute path of the new worktree.
    """
    repo_path = repos_root / repo
    if not repo_path.exists():
        raise WorktreeError(f"repo not found: {repo_path}")

    target = worktree_path(task_id, worktrees_root, repo)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Pass `--` to terminate option parsing so a base_ref that starts with `-`
    # cannot be misinterpreted as a git flag (argument injection defence).
    _run(
        ["git", "worktree", "add", "--detach", "--", str(target), base_ref],
        cwd=repo_path,
    )

    # Expose shared sccache directory via SCCACHE_DIR env var convention.
    # The actual .env file is written by vault.py; here we just log intent.
    logger.info(
        "worktree created",
        extra={"task_id": task_id, "path": str(target), "sccache_dir": str(sccache_dir)},
    )
    return target


def destroy_worktree(
    task_id: str,
    repo: str,
    repos_root: Path,
    worktrees_root: Path,
) -> None:
    """
    Remove a git worktree.  Idempotent: if the path does not exist,
    this is a no-op (state removal is handled by the caller).
    """
    repo_path = repos_root / repo
    target = worktree_path(task_id, worktrees_root, repo)

    if not target.exists():
        logger.warning("worktree path not found, skipping git remove: %s", target)
        return

    try:
        _run(["git", "worktree", "remove", "--force", str(target)], cwd=repo_path)
    except WorktreeError as exc:
        # Best-effort: log and fall through; state cleanup still happens.
        logger.warning("git worktree remove failed: %s", exc)

    logger.info("worktree destroyed", extra={"task_id": task_id, "path": str(target)})
