"""
fabric.watchdog.checks.solana_rpc
====================================
Alert class: solana_rpc

Verifies the configured Solana devnet RPC endpoint responds to a trivial
``getHealth`` JSON-RPC call within the timeout.

Config keys consumed:
    solana_rpc_url      (str)   — e.g. https://api.devnet.solana.com
    solana_rpc_timeout  (float) — HTTP timeout in seconds, default 10
"""

from __future__ import annotations

import logging
from typing import Any

from fabric.watchdog.alert_state import deterministic_alert_id
from fabric.watchdog.models import Alert, Severity
from fabric.watchdog.url_validator import validate_url

logger = logging.getLogger(__name__)

_DEFAULT_RPC_URL = "https://api.devnet.solana.com"
_DEFAULT_TIMEOUT = 10.0


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if the Solana devnet RPC is unresponsive, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("solana_rpc check failed unexpectedly: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    import urllib.error
    import urllib.request
    import json as _json

    url = config.get("solana_rpc_url", _DEFAULT_RPC_URL)
    timeout = float(config.get("solana_rpc_timeout", _DEFAULT_TIMEOUT))

    # Reject file://, ftp://, public IPs, and mainnet endpoints.
    validate_url(url, context="solana_rpc_url")

    payload = _json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "getHealth"}
    ).encode()

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _json.loads(resp.read())
        result = body.get("result", "")
        if result != "ok":
            evidence = f"Solana RPC {url!r} returned unhealthy result: {result!r}"
            logger.warning("solana_rpc: %s", evidence)
            return Alert(
                alert_class="solana_rpc",
                severity=Severity.CRITICAL,
                evidence=evidence,
                alert_id=deterministic_alert_id("solana_rpc", url),
                extra={"url": url, "result": result},
            )
        return None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        evidence = f"Solana RPC {url!r} unreachable: {exc}"
        logger.warning("solana_rpc: %s", evidence)
        return Alert(
            alert_class="solana_rpc",
            severity=Severity.CRITICAL,
            evidence=evidence,
            alert_id=deterministic_alert_id("solana_rpc", url),
            extra={"url": url, "error": str(exc)},
        )
