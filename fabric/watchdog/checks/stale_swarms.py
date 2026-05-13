"""
fabric.watchdog.checks.stale_swarms
======================================
Alert class: stale_swarms

Detects ruflo swarms that have exceeded their configured TTL.  The swarm
registry is read from RUFLO_STATE_FILE (a JSON file written by the ruflo
full install).  Swarms that have been running longer than swarm_ttl_hours
are considered stale.

Config keys consumed:
    ruflo_state_file   (str)  — path to ruflo's swarm state JSON
    swarm_ttl_hours    (int)  — max swarm age in hours, default 48
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_STATE_FILE = "/home/op/.fabric/ruflo/swarms.json"
_DEFAULT_TTL_HOURS = 48


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if stale ruflo swarms are detected, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("stale_swarms check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    state_file = Path(config.get("ruflo_state_file", _DEFAULT_STATE_FILE))
    ttl_hours = int(config.get("swarm_ttl_hours", _DEFAULT_TTL_HOURS))
    ttl_secs = ttl_hours * 3600

    if not state_file.exists():
        logger.debug("ruflo state file %s not found; skipping stale_swarms check", state_file)
        return None

    try:
        raw = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read ruflo state file %s: %s", state_file, exc)
        return None

    # Expected structure: dict of swarm_id -> {created_at: unix_timestamp, ...}
    now = time.time()
    stale = []
    for swarm_id, meta in raw.items():
        created_at = meta.get("created_at")
        if created_at is None:
            continue
        try:
            age_secs = now - float(created_at)
        except (TypeError, ValueError):
            continue
        if age_secs > ttl_secs:
            age_hours = age_secs / 3600
            stale.append(f"{swarm_id} (age {age_hours:.1f}h)")

    if not stale:
        return None

    evidence = f"{len(stale)} stale ruflo swarm(s) past {ttl_hours}h TTL: {', '.join(stale)}"
    logger.warning("stale_swarms: %s", evidence)
    return Alert(
        alert_class="stale_swarms",
        severity=Severity.WARNING,
        evidence=evidence,
        extra={"stale_swarms": stale, "ttl_hours": ttl_hours},
    )
