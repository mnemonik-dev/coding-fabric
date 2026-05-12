"""
Atomic state.json persistence.

Uses fcntl.flock (exclusive lock) + write-to-temp + atomic rename so that
a kill -9 mid-write never corrupts the state file.  Multiple concurrent
POST requests for the same task_id are safely serialised: the second
caller sees the entry already present and raises AlreadyExistsError.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import WorktreeInfo

logger = logging.getLogger(__name__)

_ISO = "%Y-%m-%dT%H:%M:%S.%f%z"


class AlreadyExistsError(Exception):
    pass


class NotFoundError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_info(raw: dict[str, Any]) -> WorktreeInfo:
    return WorktreeInfo(
        task_id=raw["task_id"],
        repo=raw["repo"],
        base_ref=raw["base_ref"],
        topic=raw["topic"],
        path=raw["path"],
        env_path=raw["env_path"],
        created_at=raw["created_at"],
        status=raw.get("status", "active"),
    )


class StateStore:
    def __init__(self, state_file: Path) -> None:
        self._path = state_file
        self._lock_path = Path(str(state_file) + ".lock")
        state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not state_file.exists():
            self._write_raw({})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_raw(self, data: dict[str, Any]) -> None:
        """Write data atomically: tempfile + fsync + rename."""
        dir_ = self._path.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_), prefix=".state-tmp-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(self._path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _read_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("state.json unreadable; returning empty state")
            return {}

    def _acquire(self):
        """Return an open file object with exclusive flock held."""
        fh = open(str(self._lock_path), "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh

    def _release(self, fh) -> None:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, info: WorktreeInfo) -> WorktreeInfo:
        """
        Add a new entry.  Raises AlreadyExistsError if task_id is present.
        Thread-safe / process-safe via flock.
        """
        fh = self._acquire()
        try:
            data = self._read_raw()
            if info.task_id in data:
                raise AlreadyExistsError(info.task_id)
            data[info.task_id] = info.model_dump(mode="json")
            self._write_raw(data)
            logger.info("state: added %s", info.task_id)
            return info
        finally:
            self._release(fh)

    def remove(self, task_id: str) -> None:
        """
        Remove an entry.  Raises NotFoundError if not present.
        """
        fh = self._acquire()
        try:
            data = self._read_raw()
            if task_id not in data:
                raise NotFoundError(task_id)
            del data[task_id]
            self._write_raw(data)
            logger.info("state: removed %s", task_id)
        finally:
            self._release(fh)

    def get(self, task_id: str) -> WorktreeInfo:
        """Return a single entry.  Raises NotFoundError if missing."""
        data = self._read_raw()
        if task_id not in data:
            raise NotFoundError(task_id)
        return _parse_info(data[task_id])

    def list_all(self) -> list[WorktreeInfo]:
        """Return all active entries."""
        data = self._read_raw()
        return [_parse_info(v) for v in data.values()]

    def count(self) -> int:
        """Return the number of active entries (no lock needed for reads)."""
        return len(self._read_raw())
