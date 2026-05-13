"""
fabric.watchdog.checks.disk_pressure
======================================
Alert class: disk_pressure

Monitors disk usage for the worktrees mount point against a 50 GB budget.
Three severity thresholds:
    75%  -> WARNING
    85%  -> WARNING (escalated)
    90%  -> CRITICAL

Config keys consumed:
    disk_path             (str)   — path to check, default /home/op/code/mnemonic-workspaces
    disk_budget_gb        (int)   — total budget in GiB, default 50
    disk_warn_threshold   (float) — first warning threshold 0-1, default 0.75
    disk_warn2_threshold  (float) — second warning threshold 0-1, default 0.85
    disk_crit_threshold   (float) — critical threshold 0-1, default 0.90
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/home/op/code/mnemonic-workspaces"
_DEFAULT_BUDGET_GB = 50
_GiB = 1024 ** 3


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if disk usage exceeds any configured threshold, else None.

    The most severe triggered threshold is reported.
    """
    try:
        return _check(config)
    except Exception as exc:
        logger.error("disk_pressure check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    path = config.get("disk_path", _DEFAULT_PATH)
    budget_bytes = int(config.get("disk_budget_gb", _DEFAULT_BUDGET_GB)) * _GiB
    warn_thresh = float(config.get("disk_warn_threshold", 0.75))
    warn2_thresh = float(config.get("disk_warn2_threshold", 0.85))
    crit_thresh = float(config.get("disk_crit_threshold", 0.90))

    try:
        stat = os.statvfs(path)
    except OSError as exc:
        logger.warning("disk_pressure: cannot stat %s: %s", path, exc)
        return None

    # Total blocks * block size gives total disk bytes.  We use total filesystem
    # size rather than only the budget so the thresholds make sense when multiple
    # consumers share the partition.
    total_bytes = stat.f_blocks * stat.f_frsize
    free_bytes = stat.f_bavail * stat.f_frsize
    used_bytes = total_bytes - free_bytes

    # Apply budget cap: report against min(total_bytes, budget_bytes).
    effective_total = min(total_bytes, budget_bytes)
    if effective_total == 0:
        return None

    ratio = used_bytes / effective_total
    used_gb = used_bytes / _GiB
    budget_gb = effective_total / _GiB

    if ratio >= crit_thresh:
        severity = Severity.CRITICAL
        label = f"{crit_thresh*100:.0f}%"
    elif ratio >= warn2_thresh:
        severity = Severity.WARNING
        label = f"{warn2_thresh*100:.0f}%"
    elif ratio >= warn_thresh:
        severity = Severity.WARNING
        label = f"{warn_thresh*100:.0f}%"
    else:
        return None

    evidence = (
        f"Disk usage at {ratio*100:.1f}% ({used_gb:.1f}/{budget_gb:.0f} GiB) — "
        f"exceeded {label} threshold on {path!r}"
    )
    logger.warning("disk_pressure: %s", evidence)
    return Alert(
        alert_class="disk_pressure",
        severity=severity,
        evidence=evidence,
        extra={
            "path": path,
            "used_gb": round(used_gb, 2),
            "budget_gb": round(budget_gb, 1),
            "ratio": round(ratio, 4),
        },
    )
