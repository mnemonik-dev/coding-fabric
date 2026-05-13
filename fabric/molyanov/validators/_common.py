"""
fabric.molyanov.validators._common
====================================
Shared utilities: severity mapping, field canonicalization, output schema
validation, and subprocess invocation helper.

All helpers are pure functions; no global state.

Security contracts
------------------
- ``extra_args`` passed to :func:`invoke_ruflo` must not contain option-style
  arguments starting with ``-``.  Any such element raises ``ValueError``
  before the subprocess is launched (argument-injection prevention).  The
  contract is also enforced via a ``--`` sentinel inserted between the plugin
  name and ``extra_args`` so that even future changes to the validation logic
  cannot silently regress.
- ``url`` values used in :mod:`post_deploy_qa_wrapper` should resolve only to
  tailnet-internal addresses.  Callers must reject ``localhost``, ``127.0.0.1``,
  ``::1``, and RFC-1918 / link-local ranges before invoking
  :func:`invoke_ruflo`.
- All string fields extracted from plugin JSON output are sanitised by
  :func:`sanitize_string`: ANSI escape sequences and non-printable control
  characters are stripped and each field is capped at 4096 bytes to prevent
  log-flooding DoS.
"""

from __future__ import annotations

import json
import logging
import re
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
    "error": "error",
    "err": "error",
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

DEFAULT_SEVERITY = "error"

_FIELD_MAX_LEN = 4096

# Matches ANSI CSI escape sequences (e.g. ESC[31m, ESC[0m)
_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# Matches non-printable control characters (exclude \t=0x09, \n=0x0a, \r=0x0d)
_RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_string(value: str) -> str:
    """Strip ANSI escapes and non-printable control chars; cap to 4096 chars.

    Args:
        value: Raw string from plugin output.

    Returns:
        Sanitized string safe for logging and structured output.
    """
    value = _RE_ANSI.sub("", value)
    value = _RE_CTRL.sub("", value)
    return value[:_FIELD_MAX_LEN]


def normalise_severity(raw: Any) -> str:
    """Return a canonical molyanov severity string from a raw plugin value.

    Falls back to ``"error"`` for unrecognised or missing values and emits a
    WARNING so operators can see unknown severity labels rather than silently
    losing critical findings.
    """
    if not isinstance(raw, str):
        return DEFAULT_SEVERITY
    canonical = SEVERITY_MAP.get(raw.lower().strip())
    if canonical is None:
        _logger.warning(
            "unrecognised severity value %r from plugin output; defaulting to %r",
            raw,
            DEFAULT_SEVERITY,
        )
        return DEFAULT_SEVERITY
    return canonical


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
    ``severity`` is passed through :func:`normalise_severity`.  All string
    fields are sanitised via :func:`sanitize_string` to strip ANSI escapes and
    control characters.

    Alias priority: canonical field names take precedence over aliases.  If both
    ``issue`` and ``message`` are present, ``issue`` wins (canonical first).

    Args:
        raw: A dict from the plugin's JSON output representing a single finding.

    Returns:
        A dict containing exactly the fields in :data:`FINDING_FIELDS`.
    """
    out: dict[str, Any] = {}

    # First pass: collect canonical fields (highest priority)
    for key, value in raw.items():
        if key in FINDING_FIELDS:
            out[key] = value

    # Second pass: fill gaps from aliases (only if canonical not already set)
    for key, value in raw.items():
        canonical = _FIELD_ALIASES.get(key)
        if canonical is not None and canonical not in out:
            out[canonical] = value

    # Normalise severity
    out["severity"] = normalise_severity(out.get("severity"))

    # Ensure all required fields exist with safe defaults
    out.setdefault("area", "general")
    out.setdefault("file", "")
    line = out.get("line")
    if line is not None:
        try:
            out["line"] = int(line)
        except (ValueError, TypeError):
            _logger.warning(
                "non-integer line value %r from plugin output; defaulting to None",
                line,
            )
            out["line"] = None
    else:
        out["line"] = None
    out.setdefault("issue", "")
    out.setdefault("fix_recommendation", "")

    # Sanitize all string fields
    for field in ("file", "issue", "fix_recommendation", "area", "severity"):
        if isinstance(out.get(field), str):
            out[field] = sanitize_string(out[field])

    return out


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------

RUFLO_TIMEOUT_SECONDS = 300
RUFLO_TIMEOUT_MAX = 1800  # hard cap: 30 minutes


def invoke_ruflo(
    plugin: str,
    named_args: list[str],
    extra_args: list[str] | None = None,
    *,
    input_data: str | None = None,
    timeout: int = RUFLO_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ``ruflo <plugin> [named_args] -- [extra_args]`` as a subprocess.

    Uses argv-form (no ``shell=True``). Caller is responsible for handling
    ``returncode``.

    ``named_args`` are structured plugin arguments that may include option flags
    such as ``--profile`` or ``--scenario`` (built by the wrapper, not
    user-supplied).

    ``extra_args`` are caller-supplied positional arguments forwarded verbatim
    after a ``--`` separator.  Any element that starts with ``-`` is rejected
    with :exc:`ValueError` (argument-injection prevention).  The ``--``
    separator also ensures that even if validation is somehow bypassed the
    arguments cannot be misinterpreted as ruflo or plugin options.

    Args:
        plugin: The ruflo plugin name, e.g. ``"jujutsu"``.
        named_args: Structured arguments built by the wrapper (may contain
            ``--flag value`` pairs).
        extra_args: User-supplied positional-only arguments.  Must not contain
            option-style strings starting with ``-``.
        input_data: Optional string sent to stdin.
        timeout: Seconds before the process is killed.  Default 300.  Capped
            at 1800 (30 minutes).

    Returns:
        A :class:`subprocess.CompletedProcess` with ``stdout`` and ``stderr``
        decoded as UTF-8.

    Raises:
        ValueError: When any element of ``extra_args`` starts with ``-`` (option
            injection prevention).
        FileNotFoundError: When the ``ruflo`` binary is not on PATH.
        subprocess.TimeoutExpired: When the process exceeds ``timeout``.
    """
    sanitized_extra: list[str] = []
    for arg in (extra_args or []):
        if isinstance(arg, str) and arg.startswith("-"):
            raise ValueError(
                f"invoke_ruflo: option-style argument {arg!r} in extra_args is "
                "not permitted; pass positional values only"
            )
        sanitized_extra.append(arg)

    effective_timeout = min(timeout, RUFLO_TIMEOUT_MAX)
    # Place -- before extra_args so they cannot be interpreted as options even
    # if the validation above is relaxed in future.
    cmd = ["ruflo", plugin, *named_args, "--", *sanitized_extra]
    _logger.debug("invoking ruflo: %s", cmd)
    return subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        text=True,
        timeout=effective_timeout,
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
