"""
fabric.watchdog.checks.irys_balance
=====================================
Alert class: irys_balance

Queries the Irys testnet node balance for the configured wallet address.
Fires if the balance drops below the configured threshold (in lamports or
the Irys native unit).

Config keys consumed:
    irys_node_url        (str)   — e.g. https://devnet.irys.xyz
    irys_address         (str)   — wallet address to check
    irys_balance_min     (int)   — minimum acceptable balance (default 1_000_000)
    irys_rpc_timeout     (float) — HTTP timeout in seconds, default 10
"""

from __future__ import annotations

import logging
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_NODE_URL = "https://devnet.irys.xyz"
_DEFAULT_MIN_BALANCE = 1_000_000
_DEFAULT_TIMEOUT = 10.0


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if the Irys testnet balance is below threshold, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("irys_balance check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    import urllib.error
    import urllib.request
    import json as _json

    node_url = config.get("irys_node_url", _DEFAULT_NODE_URL).rstrip("/")
    address = config.get("irys_address", "")
    min_balance = int(config.get("irys_balance_min", _DEFAULT_MIN_BALANCE))
    timeout = float(config.get("irys_rpc_timeout", _DEFAULT_TIMEOUT))

    if not address:
        logger.debug("irys_address not configured; skipping irys_balance check")
        return None

    url = f"{node_url}/account/balance/{address}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = _json.loads(resp.read())
        balance = int(body.get("balance", 0))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, KeyError) as exc:
        evidence = f"Irys node {node_url!r} unreachable or bad response: {exc}"
        logger.warning("irys_balance: %s", evidence)
        return Alert(
            alert_class="irys_balance",
            severity=Severity.WARNING,
            evidence=evidence,
            extra={"node_url": node_url, "error": str(exc)},
        )

    if balance < min_balance:
        evidence = (
            f"Irys testnet balance {balance} < threshold {min_balance} "
            f"for address {address[:8]}..."
        )
        logger.warning("irys_balance: %s", evidence)
        return Alert(
            alert_class="irys_balance",
            severity=Severity.WARNING,
            evidence=evidence,
            extra={"balance": balance, "min_balance": min_balance},
        )

    return None
