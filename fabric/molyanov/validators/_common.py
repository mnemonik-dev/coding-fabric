"""
fabric.molyanov.validators._common
====================================
Shared utilities: severity mapping, field canonicalization, output schema
validation, and subprocess invocation helper.

All helpers are pure functions; no global state.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any

from fabric.logs.sanitizer import SanitizedStreamHandler

# ---------------------------------------------------------------------------
# Logger — sanitized stream handler prevents raw plugin stderr reaching disk
# ---------------------------------------------------------------------------

_logger = logging.getLogger("fabric.molyanov.validators")
if not _logger.handlers:
    _logger.addHandler(SanitizedStreamHandler(sys.stderr))
    _logger.setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

FINDING_FIELDS: frozenset[str] = frozenset(
    {"severity", "area", "file", "line", "issue", "fix_recommendation"}
)

SEVERITY_MAP: dict[str, str] = {
    # generic aliases → canonical molyanov levels
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
    "info": "info",
    "informational": "info",
    "warning": "low",
    "warn": "low",
    "error": "high",
    "err": "high",
    "note": "info",
    # ruflo jujutsu
    "blocker": "critical",
    "major": "high",
    "minor": "low",
    # ruflo browser / security-audit
    "fail": "high",
    "pass": "info",
    "unknown": "info",
}

DEFAULT_SEVERITY = "info"


def normalise_severity(raw: Any) -> str:
    """Return a canonical molyanov severity string from a raw plugin value.

    Falls back to ``"info"`` for unrecognised or missing values.
    """
    if not isinstance(raw, str):
        return DEFAULT_SEVERITY
    return SEVERITY_MAP.get(raw.lower().strip(), DEFAULT_SEVERITY)


# ---------------------------------------------------------------------------
# Field canonicalization
# ---------------------------------------------------------------------------

# Common plugin aliases → molyanov canonical field names
_FIELD_ALIASES: dict[str, str] = {
    # area
    "category": "area",
    "type": "area",
    "check": "area",
    "rule": "area",
    # file
    "path": "file",
    "filename": "file",
    "source": "file",
    "location": "file",
    # line
    "line_number": "line",
    "lineno": "line",
    "start_line": "line",
    # issue
    "message": "issue",
    "msg": "issue",
    "description": "issue",
    "detail": "issue",
    "title": "issue",
    "finding": "issue",
    # fix_recommendation
    "fix": "fix_recommendation",
    "recommendation": "fix_recommendation",
    "remediation": "fix_recommendation",
    "suggestion": "fix_recommendation",
    "advice": "fix_recommendation",
    "resolution": "fix_recommendation",
}


def canonicalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate a raw plugin finding dict to the molyanov finding schema.

    Unknown fields are discarded; missing required fields receive safe defaults.
    ``severity`` is passed through :func:`normalise_severity`.

    Args:
        raw: A dict from the plugin's JSON output representing a single finding.

    Returns:
        A dict containing exactly the fields in :data:`FINDING_FIELDS`.
    """
    out: dict[str, Any] = {}

    # First pass: map aliases
    for key, value in raw.items():
        canonical = _FIELD_ALIASES.get(key, key)
        if canonical in FINDING_FIELDS and canonical not in out:
            out[canonical] = value

    # Normalise severity
    out["severity"] = normalise_severity(out.get("severity"))

    # Ensure all required fields exist with safe defaults
    out.setdefault("area", "general")
    out.setdefault("file", "")
    line = out.get("line")
    out["line"] = int(line) if line is not None else None
    out.setdefault("issue", "")
    out.setdefault("fix_recommendation", "")

    return out


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

RUFLO_TIMEOUT_SECONDS = 120


def invoke_ruflo(
    plugin: str,
    args: list[str],
    *,
    input_data: str | None = None,
    timeout: int = RUFLO_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``ruflo <plugin> [args...]`` as a subprocess.

    Uses argv-form (no ``shell=True``). Caller is responsible for handling
    ``returncode``.

    Args:
        plugin: The ruflo plugin name, e.g. ``"jujutsu"``.
        args: Additional positional arguments for the plugin.
        input_data: Optional string sent to stdin.
        timeout: Seconds before the process is killed. Default 120.

    Returns:
        A :class:`subprocess.CompletedProcess` with ``stdout`` and ``stderr``
        decoded as UTF-8.

    Raises:
        FileNotFoundError: When the ``ruflo`` binary is not on PATH.
        subprocess.TimeoutExpired: When the process exceeds ``timeout``.
    """
    cmd = ["ruflo", plugin, *args]
    _logger.debug("invoking ruflo: %s", cmd)
    return subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def parse_plugin_output(
    raw_stdout: str,
    delegated_to: str,
) -> dict[str, Any]:
    """Parse ruflo plugin JSON stdout into a molyanov result dict.

    Expects plugin to emit either:
    - A JSON array of finding objects, or
    - A JSON object with a ``"findings"`` key containing such an array.

    Args:
        raw_stdout: The decoded stdout from the ruflo plugin process.
        delegated_to: The ``delegated_to`` label to attach (e.g. ``"ruflo:jujutsu"``).

    Returns:
        ``{"findings": [...], "delegated_to": delegated_to}``
    """
    findings: list[dict[str, Any]] = []

    stripped = raw_stdout.strip()
    if stripped:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            _logger.warning(
                "ruflo plugin output is not valid JSON (%s); returning empty findings",
                exc,
            )
            data = []

        if isinstance(data, list):
            raw_findings = data
        elif isinstance(data, dict):
            raw_findings = data.get("findings", [])
        else:
            raw_findings = []

        for item in raw_findings:
            if isinstance(item, dict):
                findings.append(canonicalize_finding(item))

    return {"findings": findings, "delegated_to": delegated_to}


# ---------------------------------------------------------------------------
# Error result factory
# ---------------------------------------------------------------------------


def error_result(
    delegated_to: str,
    message: str,
    *,
    severity: str = "high",
    area: str = "validator",
) -> dict[str, Any]:
    """Return a molyanov result dict containing a single error finding.

    Used when the plugin is missing, times out, or exits non-zero without
    useful JSON output.

    Args:
        delegated_to: The ``delegated_to`` label.
        message: Human-readable description of what went wrong.
        severity: Molyanov severity level for the error finding.
        area: Finding area label.

    Returns:
        ``{"findings": [<error finding>], "delegated_to": delegated_to}``
    """
    finding: dict[str, Any] = {
        "severity": normalise_severity(severity),
        "area": area,
        "file": "",
        "line": None,
        "issue": message,
        "fix_recommendation": (
            "Ensure the ruflo CLI is installed and available on PATH. "
            "Run `which ruflo` to verify."
        ),
    }
    return {"findings": [finding], "delegated_to": delegated_to}
