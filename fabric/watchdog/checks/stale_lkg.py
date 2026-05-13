"""
fabric.watchdog.checks.stale_lkg
===================================
Alert class: stale_lkg

Verifies that the ``last-known-good`` git tag in the mnemonic-loop repo was
updated within the past stale_lkg_days (default 7 days).  A stale tag
indicates the safe-mode rollback anchor has not been refreshed after
successful task completions.

Config keys consumed:
    loop_repo_path   (str) — absolute path to mnemonic-loop local clone
    lkg_tag_name     (str) — tag name, default "last-known-good"
    stale_lkg_days   (int) — max tag age in days, default 7
    git_timeout      (int) — git subprocess timeout in seconds, default 30
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_LOOP_REPO = "/home/op/code/mnemonic-masters/mnemonic-loop"
_DEFAULT_TAG = "last-known-good"
_DEFAULT_STALE_DAYS = 7
_DEFAULT_GIT_TIMEOUT = 30


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if the last-known-good tag is stale or missing, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("stale_lkg check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    repo = Path(config.get("loop_repo_path", _DEFAULT_LOOP_REPO))
    tag_name = config.get("lkg_tag_name", _DEFAULT_TAG)
    stale_days = int(config.get("stale_lkg_days", _DEFAULT_STALE_DAYS))
    timeout = int(config.get("git_timeout", _DEFAULT_GIT_TIMEOUT))

    if not repo.is_dir():
        logger.debug("stale_lkg: loop repo %s not found; skipping", repo)
        return None

    # Get the creation date of the tag via git log --tags
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", f"refs/tags/{tag_name}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("stale_lkg: git command failed: %s", exc)
        return None

    if result.returncode != 0 or not result.stdout.strip():
        evidence = f"last-known-good tag '{tag_name}' not found in {repo.name}"
        logger.warning("stale_lkg: %s", evidence)
        return Alert(
            alert_class="stale_lkg",
            severity=Severity.WARNING,
            evidence=evidence,
            extra={"repo": str(repo), "tag": tag_name},
        )

    try:
        tag_ts = int(result.stdout.strip())
    except ValueError:
        logger.warning("stale_lkg: unexpected git output: %r", result.stdout)
        return None

    now = time.time()
    age_secs = now - tag_ts
    age_days = age_secs / 86400

    if age_days > stale_days:
        evidence = (
            f"last-known-good tag '{tag_name}' in {repo.name} is {age_days:.1f} days old "
            f"(threshold: {stale_days} days)"
        )
        logger.warning("stale_lkg: %s", evidence)
        return Alert(
            alert_class="stale_lkg",
            severity=Severity.WARNING,
            evidence=evidence,
            extra={"age_days": round(age_days, 1), "threshold_days": stale_days, "tag": tag_name},
        )

    return None
