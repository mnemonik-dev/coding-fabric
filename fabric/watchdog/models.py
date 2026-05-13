"""
fabric.watchdog.models
======================
Shared dataclasses for the watchdog alert pipeline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Single alert emitted by a check module.

    Attributes:
        alert_class: Machine-readable class name (e.g. "orphaned_worktrees").
        severity: One of Severity.INFO / WARNING / CRITICAL.
        evidence: Human-readable description (already sanitized by the check).
        alert_id: UUID4 used as idempotency key for Kaneo card creation.
        extra: Optional structured payload (not sent to Telegram).
    """

    alert_class: str
    severity: Severity
    evidence: str
    alert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "alert_class": self.alert_class,
            "severity": self.severity.value,
            "evidence": self.evidence,
            "extra": self.extra,
        }
