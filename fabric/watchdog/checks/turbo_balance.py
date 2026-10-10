"""
fabric.watchdog.checks.turbo_balance
=====================================
Alert class: turbo_balance

Queries the ArDrive Turbo payment service for the credit balance of the
operator wallet that signs Arweave uploads. Items up to 105 KiB upload for
free; larger items need Turbo credits, and the upload fails with HTTP 402
when the wallet has none. Fires if the balance (in winc, 10^-12 Credits)
drops below the configured threshold.

Config keys consumed:
    turbo_payment_url        (str)   — default https://payment.ardrive.io
    turbo_address            (str)   — operator wallet address to check
    turbo_token              (str)   — wallet type, default "solana"
    turbo_balance_min_winc   (int)   — minimum acceptable balance (default 10^11 winc)
    turbo_rpc_timeout        (float) — HTTP timeout in seconds, default 10
"""

from __future__ import annotations

import logging
from typing import Any

from fabric.watchdog.alert_state import deterministic_alert_id
from fabric.watchdog.models import Alert, Severity
from fabric.watchdog.url_validator import validate_url

logger = logging.getLogger(__name__)

_DEFAULT_PAYMENT_URL = "https://payment.ardrive.io"
_DEFAULT_TOKEN = "solana"
_DEFAULT_MIN_BALANCE = 100_000_000_000
_DEFAULT_TIMEOUT = 10.0


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if the Turbo credit balance is below threshold, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("turbo_balance check failed: %s", exc)
        return None


def _check(config: dict[str, Any]) -> Alert | None:
    import urllib.error
    import urllib.parse
    import urllib.request
    import json as _json

    payment_url = config.get("turbo_payment_url", _DEFAULT_PAYMENT_URL).rstrip("/")
    address = config.get("turbo_address", "")
    token = config.get("turbo_token", _DEFAULT_TOKEN)
    min_balance = int(config.get("turbo_balance_min_winc", _DEFAULT_MIN_BALANCE))
    timeout = float(config.get("turbo_rpc_timeout", _DEFAULT_TIMEOUT))

    if not address:
        logger.debug("turbo_address not configured; skipping turbo_balance check")
        return None

    # Reject file://, ftp://, public hosts not in the allowlist.
    validate_url(payment_url, context="turbo_payment_url")

    url = (
        f"{payment_url}/v1/account/balance/{urllib.parse.quote(token, safe='')}"
        f"?address={urllib.parse.quote_plus(address)}"
    )
    try:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = _json.loads(resp.read())
            balance = int(body.get("effectiveBalance", body.get("winc", 0)))
        except urllib.error.HTTPError as exc:
            # 404: the wallet has never held credits.
            if exc.code != 404:
                raise
            balance = 0
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        evidence = f"Turbo payment service {payment_url!r} unreachable or bad response: {exc}"
        logger.warning("turbo_balance: %s", evidence)
        return Alert(
            alert_class="turbo_balance",
            severity=Severity.WARNING,
            evidence=evidence,
            alert_id=deterministic_alert_id("turbo_balance", f"{payment_url}:{address}"),
            extra={"payment_url": payment_url, "error": str(exc)},
        )

    if balance < min_balance:
        evidence = (
            f"Turbo credit balance {balance} winc < threshold {min_balance} "
            f"for address {address[:8]}..."
        )
        logger.warning("turbo_balance: %s", evidence)
        return Alert(
            alert_class="turbo_balance",
            severity=Severity.WARNING,
            evidence=evidence,
            alert_id=deterministic_alert_id("turbo_balance", f"{payment_url}:{address}"),
            extra={"balance": balance, "min_balance": min_balance},
        )

    return None
