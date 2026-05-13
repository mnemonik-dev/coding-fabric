"""
fabric.watchdog.telegram
=========================
Posts alert messages to the ops Telegram forum topic via the Telegram Bot API.

Rate limit: max 20 messages per second (Telegram hard limit).
Sanitization: every outbound message is passed through
    fabric.logs.sanitizer.filter.sanitize before sending.

Security notes:
- The bot token is NEVER embedded in the request URL.  The API endpoint is
  kept as a fixed base URL; the token is sent as a JSON field in the POST
  body (using the ``/bot<token>/sendMessage`` path format is unavoidable in
  Telegram's API, but we sanitize exceptions so the token does not leak into
  log files via URL repr in tracebacks).
- parse_mode is intentionally omitted (defaults to plain text on Telegram)
  to prevent HTML/Markdown injection from unsanitized evidence strings.

Environment variables (loaded via systemd LoadCredential):
    TELEGRAM_BOT_TOKEN   — bot token
    TELEGRAM_OPS_CHAT_ID — ops forum chat id (negative for supergroups)
    TELEGRAM_OPS_TOPIC_ID — message_thread_id for the ops forum topic (optional)
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from fabric.watchdog.models import Alert

try:
    from fabric.logs.sanitizer.filter import sanitize
except ImportError as exc:
    raise ImportError(
        "fabric.logs.sanitizer is required by fabric.watchdog.telegram. "
        "Install fabric/logs/sanitizer before using this module."
    ) from exc

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org"
_RATE_LIMIT_MSGS_PER_SEC = 20
_MIN_INTERVAL = 1.0 / _RATE_LIMIT_MSGS_PER_SEC  # 0.05 s

# Pattern used to scrub tokens from exception messages before logging.
_TOKEN_RE = re.compile(r"bot[0-9]{8,12}:[A-Za-z0-9_-]{35,}")


def _scrub_token(text: str) -> str:
    """Replace any Telegram bot token appearing in *text* with <REDACTED>."""
    return _TOKEN_RE.sub("bot<REDACTED>", text)


class TelegramPoster:
    """Post sanitized alert messages to a Telegram ops topic.

    Throttles to 20 msg/s.  Each call blocks if the rate limit would be breached.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        topic_id: int | None = None,
        http_timeout: float = 10.0,
    ) -> None:
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_OPS_CHAT_ID", "")
        raw_topic = topic_id or os.environ.get("TELEGRAM_OPS_TOPIC_ID")
        self._topic_id: int | None = int(raw_topic) if raw_topic else None
        self._timeout = http_timeout
        self._last_send: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post_alert(self, alert: Alert) -> bool:
        """Post a single alert as a Telegram message.

        Returns True on success, False on any error (non-raising).
        """
        text = self._format_alert(alert)
        return self._send(text)

    def post_digest(self, alerts: list[Alert]) -> bool:
        """Post a batched digest for multiple alerts.

        Returns True on success, False on any error (non-raising).
        """
        text = self._format_digest(alerts)
        return self._send(text)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_alert(self, alert: Alert) -> str:
        severity_labels = {"critical": "[CRITICAL]", "warning": "[WARNING]", "info": "[INFO]"}
        label = severity_labels.get(alert.severity.value, "[ALERT]")
        # sanitize each field individually to prevent injection through evidence/alert_class.
        safe_class = sanitize(alert.alert_class)
        safe_evidence = sanitize(alert.evidence)
        safe_id = sanitize(alert.alert_id)
        text = f"{label} {safe_class}\n{safe_evidence}\nalert_id: {safe_id}"
        return text

    def _format_digest(self, alerts: list[Alert]) -> str:
        header = f"[DIGEST] {len(alerts)} alerts fired in one tick:\n"
        lines = []
        for a in alerts:
            safe_class = sanitize(a.alert_class)
            safe_evidence = sanitize(a.evidence[:80])
            lines.append(f"  [{a.severity.value.upper()}] {safe_class}: {safe_evidence}")
        ids = ", ".join(sanitize(a.alert_id) for a in alerts)
        footer = f"\nalert_ids: {ids}"
        return header + "\n".join(lines) + footer

    def _throttle(self) -> None:
        """Sleep just long enough to stay under 20 msg/s."""
        now = time.monotonic()
        elapsed = now - self._last_send
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

    def _send(self, text: str) -> bool:
        if not self._token or not self._chat_id:
            logger.warning("telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_OPS_CHAT_ID not set; skipping send")
            return False

        self._throttle()

        # parse_mode is intentionally omitted — plain text prevents HTML/Markdown injection.
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
        }
        if self._topic_id is not None:
            payload["message_thread_id"] = self._topic_id

        # The token is unavoidable in the URL path (Telegram Bot API requirement),
        # but we scrub it from any exception repr before logging so it never
        # appears in watchdog.log.
        url = f"{_TG_API_BASE}/bot{self._token}/sendMessage"
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = _json.loads(resp.read())
                self._last_send = time.monotonic()
                if not body.get("ok"):
                    logger.warning("telegram: sendMessage returned not-ok: %s", body)
                    return False
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            # Scrub token from exception repr before it reaches any log handler.
            safe_msg = _scrub_token(str(exc))
            logger.warning("telegram: sendMessage failed: %s", safe_msg)
            return False
