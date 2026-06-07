"""Atomic file-write + advisory-lock primitives for queue.jsonl.

Pattern copied from ``fabric/workspace-manager/state.py`` (StateStore._write_raw /
_acquire / _release). Deliberately NOT imported from there — the two services
must be deployable independently.

Differences from the source:
  * functions are module-level (not StateStore methods);
  * path is a parameter rather than ``self._path``;
  * ``_write_raw`` takes already-serialised bytes (we write JSONL, not JSON dict).
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from pathlib import Path
from typing import IO


def _write_raw(path: Path, raw_bytes: bytes) -> None:
    """Replace ``path`` with ``raw_bytes`` atomically.

    tempfile in the same directory + fsync(fd) + os.replace + dir fsync.
    Same-directory tempfile is required for POSIX rename atomicity.
    """
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), prefix=".queue-tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # fsync containing dir so the rename itself is durable across crashes.
    dir_fd = os.open(str(dir_), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _acquire(lock_path: Path) -> IO[str]:
    """Return an open file handle holding an exclusive flock.

    The lock file is a sibling of the data file; flock'ing the data file itself
    works but the workspace-manager convention is a separate ``.lock`` so we
    follow it (also makes it visible to lsof for ops debugging).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(str(lock_path), "w")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def _release(fh: IO[str]) -> None:
    """Release the flock and close the handle."""
    fcntl.flock(fh, fcntl.LOCK_UN)
    fh.close()
