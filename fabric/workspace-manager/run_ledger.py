"""Per-ticket run ledger: remember which (status, stage) a ticket was last
dispatched for, so the poll loop does not re-run finished work every tick.

A ticket runs again only when its key (``<status>|<stage>``) changes, or
while a failed run still has attempts left. State is a small JSON file so
the ledger survives Symphony restarts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def ledger_key(status: str, stage: str) -> str:
    return f"{status.lower()}|{stage}"


class RunLedger:
    def __init__(self, path: Path, max_attempts: int = 2) -> None:
        self._path = path
        self._max_attempts = max_attempts
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, object]] = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            logger.exception("run_ledger: cannot read %s; starting empty", self._path)
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def should_run(self, ticket_id: str, key: str) -> bool:
        with self._lock:
            entry = self._entries.get(ticket_id)
            if entry is None or entry.get("key") != key:
                return True
            return not entry.get("done")

    def forget(self, ticket_id: str) -> None:
        with self._lock:
            if self._entries.pop(ticket_id, None) is not None:
                try:
                    self._save()
                except OSError:
                    logger.exception("run_ledger: cannot write %s", self._path)

    def record(self, ticket_id: str, key: str, ok: bool) -> bool:
        """Record one finished run. Returns True when this failure used up
        the last attempt (caller should tell the operator once)."""
        with self._lock:
            entry = self._entries.get(ticket_id)
            attempts = int(entry["attempts"]) if entry and entry.get("key") == key else 0
            attempts += 1
            exhausted = not ok and attempts >= self._max_attempts
            self._entries[ticket_id] = {
                "key": key,
                "attempts": attempts,
                "done": ok or exhausted,
            }
            try:
                self._save()
            except OSError:
                logger.exception("run_ledger: cannot write %s", self._path)
            return exhausted
