"""
fabric.watchdog.turn_into_task
================================
Telegram bot command handler for /turn-into-task.

Receives a Telegram message of the form:
    /turn-into-task <alert_id>

Looks up the alert in the 24h state cache; if found, creates a Kaneo card.
Returns a user-friendly error string if the alert_id is unknown or expired.

Authorization:
    Only Telegram user_ids listed in the WATCHDOG_OPERATOR_USER_IDS environment
    variable (comma-separated integers, loaded from sops secrets) may invoke
    this command.  Any other caller receives a friendly rejection message and
    the action is dropped.

The handler is designed to be called from within the telegram-ai-agent
dispatch loop.  It does not import telegram-ai-agent directly; it only
requires that the caller passes the raw text of the command message and
a reply callback, plus the caller_user_id of the Telegram user who sent it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from fabric.watchdog.models import Alert, Severity
from fabric.watchdog.alert_state import AlertStateStore
from fabric.watchdog.kaneo import KaneoClient

try:
    from fabric.logs.sanitizer.filter import sanitize
except ImportError as exc:
    raise ImportError(
        "fabric.logs.sanitizer is required by fabric.watchdog.turn_into_task."
    ) from exc

logger = logging.getLogger(__name__)

_USAGE = (
    "Usage: /turn-into-task <alert_id>\n"
    "Example: /turn-into-task 550e8400-e29b-41d4-a716-446655440000"
)


def _load_operator_ids() -> frozenset[int]:
    """Return the set of authorized Telegram user IDs from env (sops-managed).

    The env var WATCHDOG_OPERATOR_USER_IDS is expected to be a comma-separated
    list of integer Telegram user IDs, e.g. ``123456789,987654321``.

    Returns an empty frozenset when the variable is absent — callers must treat
    an empty allowlist as "no one authorized" and reject all requests.
    """
    raw = os.environ.get("WATCHDOG_OPERATOR_USER_IDS", "").strip()
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
        else:
            logger.warning(
                "turn_into_task: ignoring non-integer in WATCHDOG_OPERATOR_USER_IDS: %r", part
            )
    return frozenset(ids)


def handle_command(
    text: str,
    config: dict[str, Any],
    reply: Callable[[str], None],
    caller_user_id: int | None = None,
    alert_store: AlertStateStore | None = None,
    kaneo_client: KaneoClient | None = None,
    alert_registry: dict[str, Alert] | None = None,
    _operator_ids: frozenset[int] | None = None,
) -> None:
    """Process a /turn-into-task command from Telegram.

    Args:
        text: Full text of the command message (e.g. "/turn-into-task abc-123").
        config: Watchdog configuration dict (same as scheduler uses).
        reply: Callable that sends a reply message back to the Telegram user.
        caller_user_id: Telegram user_id of the message sender.  Required for
            authorization; if None the command is rejected.
        alert_store: Optional AlertStateStore; if None, one is constructed from config.
        kaneo_client: Optional KaneoClient; if None, one is constructed from config.
        alert_registry: Optional in-memory map of alert_id -> Alert for the
            current process lifetime.  Used to reconstruct Alert objects from state.
        _operator_ids: Override operator id set (used in tests only).
    """
    # --- Authorization check -------------------------------------------------
    operator_ids = _operator_ids if _operator_ids is not None else _load_operator_ids()

    if not operator_ids:
        logger.error(
            "turn_into_task: WATCHDOG_OPERATOR_USER_IDS is empty; rejecting all requests"
        )
        reply("This command is not available: no authorized operators configured.")
        return

    if caller_user_id is None or caller_user_id not in operator_ids:
        logger.warning(
            "turn_into_task: unauthorized caller user_id=%s attempted /turn-into-task",
            caller_user_id,
        )
        reply("You are not authorized to use this command.")
        return

    # --- Argument parsing ----------------------------------------------------
    parts = text.strip().split()
    if len(parts) < 2:
        reply(sanitize(_USAGE))
        return

    alert_id = parts[1].strip()

    if alert_store is None:
        state_path = Path(
            config.get("alert_state_file", "/home/op/.fabric/watchdog/state/alerts.json")
        )
        alert_store = AlertStateStore(state_path)

    if not alert_store.is_seen(alert_id):
        msg = (
            f"Unknown or expired alert_id: {alert_id!r}\n"
            "Alert IDs are valid for 24 hours after the alert was first fired.\n"
            "Run without arguments for usage:\n" + _USAGE
        )
        reply(sanitize(msg))
        return

    if kaneo_client is None:
        kaneo_client = KaneoClient()

    # --- Reconstruct Alert from registry or state ----------------------------
    alert: Alert | None = None
    if alert_registry:
        alert = alert_registry.get(alert_id)

    if alert is None:
        # Best-effort: look up alert_class from the state store so the
        # placeholder is bound to the correct class (prevents mismatched cards).
        stored_class = alert_store.get_alert_class(alert_id)
        alert = Alert(
            alert_id=alert_id,
            alert_class=stored_class if stored_class else "unknown",
            severity=Severity.INFO,
            evidence="Alert details not available (reconstructed from state cache).",
        )
        logger.warning(
            "turn_into_task: alert_id %s found in state but not in registry; "
            "using placeholder with alert_class=%s",
            alert_id,
            alert.alert_class,
        )

    card = kaneo_client.create_card(alert)
    if card:
        card_id = card.get("id", "?")
        title = card.get("title", alert.alert_class)
        reply(sanitize(f"Kaneo card created: [{title}] id={card_id}"))
        logger.info(
            "turn_into_task: created Kaneo card id=%s for alert_id=%s",
            card_id,
            alert_id,
        )
    else:
        reply(sanitize(
            "Failed to create Kaneo card. Check KANEO_BASE_URL and KANEO_API_TOKEN "
            "environment variables or Kaneo server logs."
        ))
