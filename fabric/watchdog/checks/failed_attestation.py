"""
fabric.watchdog.checks.failed_attestation
============================================
Alert class: failed_attestation

Checks whether the Mnemonic MCP pending-queue contains failed attestations.
Calls the ``mnemonic_check_pending`` MCP tool via a local HTTP endpoint.

Config keys consumed:
    mnemonic_mcp_url    (str)   — base URL of the local Mnemonic MCP server
                                   e.g. http://127.0.0.1:4000
    mnemonic_api_key    (str)   — bearer token if required (optional)
    mcp_timeout         (float) — HTTP timeout in seconds, default 10
"""

from __future__ import annotations

import json as _json
import logging
import urllib.error
import urllib.request
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_MCP_URL = "http://127.0.0.1:4000"
_DEFAULT_TIMEOUT = 10.0


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if the Mnemonic MCP queue has failures, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("failed_attestation check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    base_url = config.get("mnemonic_mcp_url", _DEFAULT_MCP_URL).rstrip("/")
    api_key = config.get("mnemonic_api_key", "")
    timeout = float(config.get("mcp_timeout", _DEFAULT_TIMEOUT))

    # Call the MCP tool endpoint: POST /mcp with tool call for mnemonic_check_pending
    payload = _json.dumps({
        "method": "tools/call",
        "params": {
            "name": "mnemonic_check_pending",
            "arguments": {},
        },
    }).encode()

    url = f"{base_url}/mcp"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        evidence = f"Mnemonic MCP server {base_url!r} unreachable: {exc}"
        logger.warning("failed_attestation: %s", evidence)
        return Alert(
            alert_class="failed_attestation",
            severity=Severity.WARNING,
            evidence=evidence,
            extra={"mcp_url": base_url, "error": str(exc)},
        )

    # Response shape: {result: {content: [{type: "text", text: "<json>"}]}}
    failures: list[dict] = []
    try:
        content = body.get("result", {}).get("content", [])
        for item in content:
            if item.get("type") == "text":
                data = _json.loads(item["text"])
                pending = data if isinstance(data, list) else data.get("pending", [])
                failures = [
                    e for e in pending
                    if isinstance(e, dict) and e.get("status") in ("failed", "error")
                ]
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("failed_attestation: unexpected MCP response shape: %s", exc)
        return None

    if not failures:
        return None

    ids = [f.get("id", "?") for f in failures[:5]]
    evidence = (
        f"{len(failures)} failed Mnemonic attestation(s) in MCP queue: "
        f"{', '.join(ids)}"
    )
    if len(failures) > 5:
        evidence += f" ... and {len(failures) - 5} more"
    logger.warning("failed_attestation: %s", evidence)
    return Alert(
        alert_class="failed_attestation",
        severity=Severity.CRITICAL,
        evidence=evidence,
        extra={"failures": failures[:10]},
    )
