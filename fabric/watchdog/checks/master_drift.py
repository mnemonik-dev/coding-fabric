"""
fabric.watchdog.checks.master_drift
======================================
Alert class: master_drift

Checks whether local master (main) HEAD has diverged from origin/master
(or origin/main) for each configured repository clone.  Divergence means
the local ref and the remote ref point to different commits.

Only ``git fetch`` + ``git rev-parse`` are run; no merge or pull.

Config keys consumed:
    master_repos   (list[str]) — absolute paths to local git repos to check
                                  default: [/home/op/code/mnemonic-masters/...]
    master_branch  (str)       — branch name, default "main"
    git_timeout    (int)       — git subprocess timeout in seconds, default 30
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_BRANCH = "main"
_DEFAULT_REPOS: list[str] = [
    "/home/op/code/mnemonic-masters/mnemonic-core",
    "/home/op/code/mnemonic-masters/mnemonic-mcp",
    "/home/op/code/mnemonic-masters/mnemonic-wasm",
    "/home/op/code/mnemonic-masters/mnemonic-loop",
]
_DEFAULT_GIT_TIMEOUT = 30


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if any configured repo's master has drifted from origin, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("master_drift check failed: %s", exc)
        return None


def _rev_parse(repo: Path, ref: str, timeout: int) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", ref],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _fetch(repo: Path, timeout: int) -> bool:
    try:
        result = subprocess.run(
            ["git", "fetch", "--quiet", "origin"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo),
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check(config: dict[str, Any]) -> Alert | None:
    repos = [Path(p) for p in config.get("master_repos", _DEFAULT_REPOS)]
    branch = config.get("master_branch", _DEFAULT_BRANCH)
    timeout = int(config.get("git_timeout", _DEFAULT_GIT_TIMEOUT))

    drifted = []
    for repo in repos:
        if not repo.is_dir():
            logger.debug("master_drift: repo %s not found; skipping", repo)
            continue

        _fetch(repo, timeout)

        local_sha = _rev_parse(repo, branch, timeout)
        remote_sha = _rev_parse(repo, f"origin/{branch}", timeout)

        if local_sha is None or remote_sha is None:
            logger.debug(
                "master_drift: could not resolve refs for %s (local=%s remote=%s)",
                repo,
                local_sha,
                remote_sha,
            )
            continue

        if local_sha != remote_sha:
            drifted.append(
                f"{repo.name} (local={local_sha[:8]} origin={remote_sha[:8]})"
            )

    if not drifted:
        return None

    evidence = f"{len(drifted)} repo(s) local master diverged from origin: {', '.join(drifted)}"
    logger.warning("master_drift: %s", evidence)
    return Alert(
        alert_class="master_drift",
        severity=Severity.WARNING,
        evidence=evidence,
        extra={"drifted": drifted, "branch": branch},
    )
