"""
fabric.watchdog.checks.hung_tmux
==================================
Alert class: hung_tmux

Detects tmux sessions that have been idle for more than the configured
threshold (default 12 hours).  Idle time is derived from the mtime of the
tmux socket file under /tmp/tmux-<uid>/ or via ``tmux list-sessions`` output.

Strategy: run ``tmux list-sessions -F '#{session_name} #{session_activity}'``
and compare session_activity timestamps to now.  If tmux is not installed the
check is a no-op.

Config keys consumed:
    tmux_idle_hours  (int)   — threshold in hours, default 12
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_IDLE_HOURS = 12


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if any tmux session has been idle > threshold, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("hung_tmux check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    threshold_hours = int(config.get("tmux_idle_hours", _DEFAULT_IDLE_HOURS))
    threshold_secs = threshold_hours * 3600

    if not shutil.which("tmux"):
        logger.debug("tmux not found; skipping hung_tmux check")
        return None

    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        logger.warning("tmux list-sessions timed out")
        return None

    if result.returncode != 0:
        # No sessions running is not an error.
        return None

    now = time.time()
    hung = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            activity_ts = int(parts[1])
        except ValueError:
            continue
        idle_secs = now - activity_ts
        if idle_secs > threshold_secs:
            idle_hours = idle_secs / 3600
            hung.append(f"{name} (idle {idle_hours:.1f}h)")

    if not hung:
        return None

    evidence = f"{len(hung)} hung tmux session(s) idle >{threshold_hours}h: {', '.join(hung)}"
    logger.warning("hung_tmux: %s", evidence)
    return Alert(
        alert_class="hung_tmux",
        severity=Severity.WARNING,
        evidence=evidence,
        extra={"hung_sessions": hung, "threshold_hours": threshold_hours},
    )
