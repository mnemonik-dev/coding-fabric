"""
fabric.watchdog.telegram
=========================
Posts alert messages to the ops Telegram forum topic via the Telegram Bot API.

Rate limit: max 20 messages per second (Telegram hard limit).
Sanitization: every outbound message is passed through
    fabric.logs.sanitizer.filter.sanitize before sending.

Environment variables (loaded via systemd LoadCredential):
    TELEGRAM_BOT_TOKEN   — bot token
    TELEGRAM_OPS_CHAT_ID — ops forum chat id (negative for supergroups)
    TELEGRAM_OPS_TOPIC_ID — message_thread_id for the ops forum topic (optional)
"""

from __future__ import annotations

import json as _json
import logging
import os
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

_TG_API = "https://api.telegram.org/bot{token}/sendMessage"
_RATE_LIMIT_MSGS_PER_SEC = 20
_MIN_INTERVAL = 1.0 / _RATE_LIMIT_MSGS_PER_SEC  # 0.05 s


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
        severity_emoji = {"critical": "[CRITICAL]", "warning": "[WARNING]", "info": "[INFO]"}
        label = severity_emoji.get(alert.severity.value, "[ALERT]")
        text = (
            f"{label} {alert.alert_class}\n"
            f"{alert.evidence}\n"
            f"alert_id: {alert.alert_id}"
        )
        return sanitize(text)

    def _format_digest(self, alerts: list[Alert]) -> str:
        header = f"[DIGEST] {len(alerts)} alerts fired in one tick:\n"
        lines = []
        for a in alerts:
            lines.append(f"  [{a.severity.value.upper()}] {a.alert_class}: {a.evidence[:80]}")
        ids = ", ".join(a.alert_id for a in alerts)
        footer = f"\nalert_ids: {ids}"
        text = header + "\n".join(lines) + footer
        return sanitize(text)

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

        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if self._topic_id is not None:
            payload["message_thread_id"] = self._topic_id

        url = _TG_API.format(token=self._token)
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
            logger.warning("telegram: sendMessage failed: %s", exc)
            return False
