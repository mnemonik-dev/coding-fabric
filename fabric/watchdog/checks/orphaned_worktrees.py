"""
fabric.watchdog.checks.orphaned_worktrees
==========================================
Alert class: orphaned_worktrees

Detects disk directories under WORKTREES_ROOT that are not tracked by
workspace-manager state.json.  An orphan wastes disk space and may indicate
a crashed cleanup path.

Config keys consumed:
    worktrees_root  (str)  — e.g. /home/op/code/mnemonic-workspaces
    state_file      (str)  — absolute path to workspace-manager/state.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if orphaned worktree directories are found, else None.

    Exceptions are caught internally; a failed check returns None and logs the
    error so the scheduler continues with other checks.
    """
    try:
        return _check(config)
    except Exception as exc:
        logger.error("orphaned_worktrees check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    root = Path(config.get("worktrees_root", "/home/op/code/mnemonic-workspaces"))
    state_file = Path(config.get("state_file", "/home/op/.fabric/workspace-manager/state.json"))

    if not root.exists():
        logger.debug("worktrees_root %s does not exist; skipping orphan check", root)
        return None

    known_paths: set[str] = set()
    if state_file.exists():
        try:
            raw = json.loads(state_file.read_text())
            for entry in raw.values():
                p = entry.get("path", "")
                if p:
                    known_paths.add(str(Path(p).resolve()))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("could not read state_file %s: %s", state_file, exc)

    orphans = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        resolved = str(item.resolve())
        if resolved not in known_paths:
            orphans.append(item.name)

    if not orphans:
        return None

    evidence = f"{len(orphans)} orphaned worktree dir(s): {', '.join(sorted(orphans))}"
    logger.warning("orphaned_worktrees: %s", evidence)
    return Alert(
        alert_class="orphaned_worktrees",
        severity=Severity.WARNING,
        evidence=evidence,
        extra={"orphans": sorted(orphans)},
    )
