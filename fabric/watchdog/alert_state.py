"""
fabric.watchdog.alert_state
============================
Persistent alert-id cache stored in state/alerts.json.

TTL: 24 hours.  An alert_id already in the cache within 24h is considered
"already notified" so the same alert is not re-posted to Telegram on every
scheduler tick.

The file is read/written with an exclusive flock for process-safety (the
systemd timer may overlap with a manual run).

State file mode: 0640 (owner rw, group r, world none) — enforced after
every write because os.replace() on Linux resets permissions to 0644.

alert_class binding: each entry stores the alert_class alongside recorded_at
so that /turn-into-task can verify the caller is not recycling an alert_id
with a mismatched class.

TOCTOU: is_seen() acquires the same flock as mark_seen() so a concurrent
check-and-set cannot race.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_TTL_SECONDS = 86400  # 24 hours
_STATE_FILE_MODE = 0o640


def deterministic_alert_id(alert_class: str, infrastructure_signature: str = "") -> str:
    """Return a stable alert_id for transient-condition alerts.

    Using a deterministic id (SHA-256 truncated to 64 hex chars) prevents
    alert_id rotation when the same infrastructure outage persists beyond one
    scheduler tick, capping repeated Telegram notifications to at most 1 per 24h
    for the same condition.

    Args:
        alert_class: The alert class name (e.g. "solana_rpc").
        infrastructure_signature: An additional string that distinguishes the
            specific infrastructure instance (e.g. node URL, swarm id).  Pass
            an empty string for checks that are inherently singleton.

    Returns:
        A 64-character lowercase hex string.
    """
    raw = f"{alert_class}:{infrastructure_signature}"
    return hashlib.sha256(raw.encode()).hexdigest()


class AlertStateStore:
    """JSON-backed TTL cache for seen alert IDs.

    Each entry stores::

        {
            "recorded_at": <unix timestamp>,
            "alert_class": "<class name>"
        }

    The alert_class field is used by :meth:`get_alert_class` and
    :meth:`check_seen_with_class` to validate /turn-into-task requests.
    """

    def __init__(self, state_file: Path) -> None:
        self._path = state_file
        self._lock_path = Path(str(state_file) + ".lock")
        state_file.parent.mkdir(parents=True, exist_ok=True)
        if not state_file.exists():
            fh = self._acquire()
            try:
                self._write_raw({})
            finally:
                self._release(fh)

    def is_seen(self, alert_id: str) -> bool:
        """Return True if alert_id was recorded within the last 24h.

        Acquires the flock so this cannot race with mark_seen() (TOCTOU fix).
        """
        fh = self._acquire()
        try:
            data = self._read_raw()
            return self._entry_is_fresh(data, alert_id)
        finally:
            self._release(fh)

    def mark_seen(self, alert_id: str, alert_class: str = "") -> None:
        """Record alert_id as seen (or refresh its TTL).

        Args:
            alert_id: The alert identifier.
            alert_class: The alert class name; stored alongside recorded_at so
                /turn-into-task can bind the card to the correct class.
        """
        fh = self._acquire()
        try:
            data = self._read_raw()
            data[alert_id] = {
                "recorded_at": time.time(),
                "alert_class": alert_class,
            }
            # Evict expired entries to keep the file small.
            data = {
                k: v for k, v in data.items()
                if (time.time() - v.get("recorded_at", 0)) < _TTL_SECONDS
            }
            self._write_raw(data)
        finally:
            self._release(fh)

    def check_seen_with_class(self, alert_id: str, expected_class: str) -> bool:
        """Return True only if alert_id is fresh AND bound to expected_class.

        Used by /turn-into-task to reject mismatched alert_id / alert_class
        combinations (prevents an attacker from recycling an alert_id to create
        a card for an arbitrary class).

        When the stored entry has no alert_class (legacy entries without class
        binding), the check falls back to is_seen() semantics (class is not
        enforced) to maintain backward compatibility.
        """
        fh = self._acquire()
        try:
            data = self._read_raw()
            if not self._entry_is_fresh(data, alert_id):
                return False
            stored_class = data[alert_id].get("alert_class", "")
            if stored_class and stored_class != expected_class:
                logger.warning(
                    "alert_state: alert_id %s class mismatch: stored=%r expected=%r",
                    alert_id,
                    stored_class,
                    expected_class,
                )
                return False
            return True
        finally:
            self._release(fh)

    def get_alert_class(self, alert_id: str) -> str | None:
        """Return the stored alert_class for alert_id, or None if not found/expired."""
        fh = self._acquire()
        try:
            data = self._read_raw()
            if not self._entry_is_fresh(data, alert_id):
                return None
            return data[alert_id].get("alert_class") or None
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
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_is_fresh(data: dict[str, Any], alert_id: str) -> bool:
        entry = data.get(alert_id)
        if entry is None:
            return False
        recorded_at = entry.get("recorded_at", 0)
        return (time.time() - recorded_at) < _TTL_SECONDS

    def _write_raw(self, data: dict[str, Any]) -> None:
        dir_ = self._path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_), prefix=".alerts-tmp-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            # Enforce 0640 on the temp file before rename so the final file
            # inherits the correct permissions atomically.
            os.chmod(tmp_path, _STATE_FILE_MODE)
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
