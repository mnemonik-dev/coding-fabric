"""
fabric.watchdog.kaneo
======================
Creates Kaneo task cards from watchdog alerts.

Card creation is idempotent by alert_id — the alert_id is stored in the card
description so that repeated calls for the same alert do not create duplicates.
The Kaneo HTTP API is queried first to check for an existing card with the
same alert_id; if found, no new card is created.

Environment variables (loaded via systemd LoadCredential):
    KANEO_BASE_URL   — e.g. http://127.0.0.1:3000
    KANEO_API_TOKEN  — bearer token
    KANEO_PROJECT_ID — default project to file alerts under
"""

from __future__ import annotations

import json as _json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from fabric.watchdog.models import Alert

try:
    from fabric.logs.sanitizer.filter import sanitize
except ImportError as exc:
    raise ImportError(
        "fabric.logs.sanitizer is required by fabric.watchdog.kaneo."
    ) from exc

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


class KaneoClient:
    """Thin HTTP client for the Kaneo task-tracker API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        project_id: str | None = None,
        http_timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = (base_url or os.environ.get("KANEO_BASE_URL", "")).rstrip("/")
        self._token = api_token or os.environ.get("KANEO_API_TOKEN", "")
        self._project_id = project_id or os.environ.get("KANEO_PROJECT_ID", "watchdog")
        self._timeout = http_timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_card(self, alert: Alert) -> dict[str, Any] | None:
        """Create a Kaneo card for the given alert.

        Idempotent: if a card with this alert_id already exists in the project,
        returns the existing card's metadata without creating a duplicate.

        Returns the card payload dict on success, None on error (non-raising).
        """
        try:
            return self._create_card(alert)
        except Exception as exc:
            logger.error("kaneo.create_card failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> dict[str, Any] | list | None:
        if not self._base_url:
            logger.warning("kaneo: KANEO_BASE_URL not set; skipping")
            return None
        url = f"{self._base_url}{path}"
        data = _json.dumps(body).encode() if body is not None else None
        headers: dict[str, str] = {"Accept": "application/json"}
        if data:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return _json.loads(resp.read())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            logger.warning("kaneo: %s %s failed: %s", method, path, exc)
            return None

    def _existing_card(self, alert_id: str) -> dict | None:
        """Search the project for a card that has alert_id in its description."""
        result = self._request("GET", f"/api/tasks?project={self._project_id}&q=alert_id%3A{alert_id}")
        if not isinstance(result, list):
            return None
        for card in result:
            desc = card.get("description", "")
            if f"alert_id: {alert_id}" in desc:
                return card
        return None

    def _create_card(self, alert: Alert) -> dict[str, Any] | None:
        # Idempotency check
        existing = self._existing_card(alert.alert_id)
        if existing:
            logger.debug("kaneo: card already exists for alert_id %s", alert.alert_id)
            return existing

        title = sanitize(f"[{alert.severity.value.upper()}] {alert.alert_class}")
        description = sanitize(
            f"{alert.evidence}\n\nalert_id: {alert.alert_id}"
        )
        payload = {
            "title": title,
            "description": description,
            "project": self._project_id,
            "priority": "high" if alert.severity.value == "critical" else "medium",
            "labels": ["watchdog", alert.alert_class],
        }
        result = self._request("POST", "/api/tasks", body=payload)
        if result:
            logger.info(
                "kaneo: created card for alert_id=%s alert_class=%s",
                alert.alert_id,
                alert.alert_class,
            )
        return result if isinstance(result, dict) else None
