"""
Unit tests for state.py — atomic persistence, locking, error cases.

TDD anchors:
  - test_state_atomic (kill -9 mid-write doesn't corrupt)
  - test_concurrent_post_same_task_id_returns_409 (flock race)
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import WorktreeInfo
from state import AlreadyExistsError, NotFoundError, StateStore


def _make_info(task_id: str = "TEST-001", topic: str = "docs") -> WorktreeInfo:
    return WorktreeInfo(
        task_id=task_id,
        repo="mnemonic-docs",
        base_ref="main",
        topic=topic,
        path=f"/tmp/worktrees/{task_id}/mnemonic-docs",
        env_path=f"/tmp/worktrees/{task_id}/mnemonic-docs/.env",
        created_at=datetime.now(timezone.utc),
    )


def test_add_and_get(tmp_path):
    store = StateStore(tmp_path / "state.json")
    info = _make_info()
    store.add(info)
    got = store.get("TEST-001")
    assert got.task_id == "TEST-001"
    assert got.topic == "docs"


def test_add_duplicate_raises(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add(_make_info())
    with pytest.raises(AlreadyExistsError):
        store.add(_make_info())


def test_remove(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add(_make_info())
    store.remove("TEST-001")
    with pytest.raises(NotFoundError):
        store.get("TEST-001")


def test_remove_not_found(tmp_path):
    store = StateStore(tmp_path / "state.json")
    with pytest.raises(NotFoundError):
        store.remove("GHOST-999")


def test_list_all(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add(_make_info("ALPHA-01"))
    store.add(_make_info("BETA-02"))
    items = store.list_all()
    ids = {i.task_id for i in items}
    assert "ALPHA-01" in ids
    assert "BETA-02" in ids


def test_count(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.count() == 0
    store.add(_make_info("X-001"))
    assert store.count() == 1
    store.add(_make_info("X-002"))
    assert store.count() == 2
    store.remove("X-001")
    assert store.count() == 1


def test_state_atomic_no_partial_write(tmp_path):
    """
    Simulate an abrupt termination mid-write by examining the temp file pattern.
    The actual atomic guarantee is: os.replace() is atomic on POSIX —
    if the process dies before os.replace(), the original state.json is intact;
    if it dies after, the new state.json is intact.

    We verify that state.json is always valid JSON after a sequence of writes.
    """
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)

    for i in range(20):
        info = _make_info(f"TASK-{i:03d}")
        store.add(info)
        # Read directly from disk and confirm valid JSON
        raw = json.loads(state_file.read_text())
        assert isinstance(raw, dict)
        assert len(raw) == i + 1


def test_state_survives_interrupted_write(tmp_path):
    """
    Place a deliberately corrupted temp file alongside state.json and confirm
    that the store still reads the last good state correctly.
    """
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)
    store.add(_make_info("GOOD-001"))

    # Simulate a leftover tmp file (as if kill -9 happened during write)
    leftovers = tmp_path / ".state-tmp-corrupt"
    leftovers.write_text("{{{broken json")

    # Store must still work
    assert store.get("GOOD-001").task_id == "GOOD-001"
    assert store.count() == 1


# ---------------------------------------------------------------------------
# Concurrency: two processes racing to add same task_id
# ---------------------------------------------------------------------------

def _worker_add(state_path_str: str, task_id: str, result_queue):
    """Worker function for multiprocessing test."""
    sys.path.insert(0, str(Path(state_path_str).parent.parent.parent.parent))
    ROOT2 = Path(__file__).parent.parent
    if str(ROOT2) not in sys.path:
        sys.path.insert(0, str(ROOT2))
    from state import StateStore, AlreadyExistsError
    from models import WorktreeInfo
    from datetime import datetime, timezone

    store = StateStore(Path(state_path_str))
    info = WorktreeInfo(
        task_id=task_id,
        repo="mnemonic-docs",
        base_ref="main",
        topic="docs",
        path="/tmp/x",
        env_path="/tmp/x/.env",
        created_at=datetime.now(timezone.utc),
    )
    try:
        store.add(info)
        result_queue.put("ok")
    except AlreadyExistsError:
        result_queue.put("conflict")
    except Exception as exc:
        result_queue.put(f"error:{exc}")


def test_concurrent_add_same_task_id(tmp_path):
    """Two processes racing to add the same task_id: exactly one succeeds."""
    state_file = tmp_path / "state.json"
    StateStore(state_file)  # initialise empty state

    q: multiprocessing.Queue = multiprocessing.Queue()
    p1 = multiprocessing.Process(target=_worker_add, args=(str(state_file), "RACE-001", q))
    p2 = multiprocessing.Process(target=_worker_add, args=(str(state_file), "RACE-001", q))

    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    results = [q.get_nowait() for _ in range(2)]
    assert sorted(results) == ["conflict", "ok"], f"Unexpected results: {results}"
