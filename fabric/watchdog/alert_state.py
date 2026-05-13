"""
fabric.watchdog.alert_state
============================
Persistent alert-id cache stored in state/alerts.json.

TTL: 24 hours.  An alert_id already in the cache within 24h is considered
"already notified" so the same alert is not re-posted to Telegram on every
scheduler tick.

The file is read/written with an exclusive flock for process-safety (the
systemd timer may overlap with a manual run).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SECONDS = 86400  # 24 hours


class AlertStateStore:
    """JSON-backed TTL cache for seen alert IDs."""

    def __init__(self, state_file: Path) -> None:
        self._path = state_file
        self._lock_path = Path(str(state_file) + ".lock")
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if not state_file.exists():
            self._write_raw({})

    def is_seen(self, alert_id: str) -> bool:
        """Return True if alert_id was recorded within the last 24h."""
        data = self._read_raw()
        entry = data.get(alert_id)
        if entry is None:
            return False
        recorded_at = entry.get("recorded_at", 0)
        return (time.time() - recorded_at) < _TTL_SECONDS

    def mark_seen(self, alert_id: str) -> None:
        """Record alert_id as seen (or refresh its TTL)."""
        fh = self._acquire()
        try:
            data = self._read_raw()
            data[alert_id] = {"recorded_at": time.time()}
            # Evict expired entries to keep the file small.
            data = {
                k: v for k, v in data.items()
                if (time.time() - v.get("recorded_at", 0)) < _TTL_SECONDS
            }
            self._write_raw(data)
        finally:
            self._release(fh)

    def clear_expired(self) -> int:
        """Remove expired entries.  Returns the number removed."""
        fh = self._acquire()
        try:
            data = self._read_raw()
            now = time.time()
            fresh = {k: v for k, v in data.items() if (now - v.get("recorded_at", 0)) < _TTL_SECONDS}
            removed = len(data) - len(fresh)
            if removed:
                self._write_raw(fresh)
            return removed
        finally:
            self._release(fh)

    # ------------------------------------------------------------------
    # Internal helpers (same atomic write pattern as workspace-manager)
    # ------------------------------------------------------------------

    def _write_raw(self, data: dict[str, Any]) -> None:
        dir_ = self._path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_), prefix=".alerts-tmp-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(self._path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        dir_fd = os.open(str(dir_), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _read_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("alerts.json unreadable; returning empty state")
            return {}

    def _acquire(self):
        fh = open(str(self._lock_path), "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    def _release(self, fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
