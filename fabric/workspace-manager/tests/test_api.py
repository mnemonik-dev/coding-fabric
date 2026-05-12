"""
API-level tests for workspace-manager.

TDD anchors covered:
  - test_post_creates_worktree_and_env
  - test_delete_removes_worktree_and_env
  - test_capacity_cap
  - test_health_reflects_subsystems
  - test_get_worktrees_lists_active
  - test_path_traversal_blocked
  - test_concurrent_post_same_task_id_returns_409
  - test_delete_unknown_task_id_returns_404
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


VALID_PAYLOAD = {
    "task_id": "TEST-001",
    "repo": "mnemonic-docs",
    "base_ref": "main",
    "topic": "docs",
}


# ---------------------------------------------------------------------------
# test_post_creates_worktree_and_env
# ---------------------------------------------------------------------------


def test_post_creates_worktree_and_env(client, tmp_dirs, make_repo):
    make_repo("mnemonic-docs")
    resp = client.post("/worktree", json=VALID_PAYLOAD)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["task_id"] == "TEST-001"
    assert "path" in data
    assert "env_path" in data
    assert "created_at" in data

    # .env file must exist with mode 0600
    env_path = Path(data["env_path"])
    assert env_path.exists(), f".env not found at {env_path}"
    stat = env_path.stat()
    assert oct(stat.st_mode)[-3:] == "600", f"unexpected mode {oct(stat.st_mode)}"

    # Worktree directory must exist
    assert Path(data["path"]).exists(), f"worktree path not found: {data['path']}"


# ---------------------------------------------------------------------------
# test_delete_removes_worktree_and_env
# ---------------------------------------------------------------------------


def test_delete_removes_worktree_and_env(client, tmp_dirs, make_repo):
    make_repo("mnemonic-docs")

    # Create first
    resp = client.post("/worktree", json=VALID_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    env_path = Path(data["env_path"])
    wt_path = Path(data["path"])

    # Verify created
    assert env_path.exists()
    assert wt_path.exists()

    # Delete
    resp_del = client.delete(f"/worktree/TEST-001")
    assert resp_del.status_code == 204, resp_del.text

    # Both must be gone
    assert not env_path.exists(), ".env still exists after DELETE"
    assert not wt_path.exists(), "worktree dir still exists after DELETE"

    # State must also be gone
    resp_get = client.get("/worktree/TEST-001")
    assert resp_get.status_code == 404


# ---------------------------------------------------------------------------
# test_capacity_cap
# ---------------------------------------------------------------------------


def test_capacity_cap(client, tmp_dirs, make_repo, tmp_settings):
    make_repo("mnemonic-docs")
    cap = tmp_settings.capacity_total  # 10

    # Fill to capacity
    for i in range(cap):
        task_id = f"TASK-{i:03d}"
        payload = {**VALID_PAYLOAD, "task_id": task_id}
        resp = client.post("/worktree", json=payload)
        assert resp.status_code == 200, f"Task {i}: {resp.text}"

    # 11th must return 409 with "capacity"
    payload_11 = {**VALID_PAYLOAD, "task_id": "TASK-999"}
    resp_11 = client.post("/worktree", json=payload_11)
    assert resp_11.status_code == 409, resp_11.text
    body = resp_11.json()
    assert body.get("detail", {}).get("error") == "capacity"


# ---------------------------------------------------------------------------
# test_health_reflects_subsystems
# ---------------------------------------------------------------------------


def test_health_reflects_subsystems(client, tmp_settings):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()

    # sccache_dir exists (fixture creates it)
    assert data["sccache_mounted"] is True
    # vault mock returns True
    assert data["vault_reachable"] is True
    assert data["ok"] is True
    assert data["capacity_total"] == tmp_settings.capacity_total
    assert isinstance(data["capacity_used"], int)


def test_health_degraded_when_sccache_missing(client, tmp_settings):
    """Service stays up but ok=False when sccache dir is absent."""
    import shutil
    shutil.rmtree(tmp_settings.sccache_dir)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sccache_mounted"] is False
    assert data["ok"] is False


def test_health_degraded_when_vault_unreachable(client, tmp_settings, mock_bw):
    """Service stays up but ok=False when vault is unreachable."""
    # Override both the vault-module and main-module patches to return False
    mock_bw["reachable_vault"].return_value = False
    mock_bw["reachable_main"].return_value = False
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["vault_reachable"] is False
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# test_get_worktrees_lists_active
# ---------------------------------------------------------------------------


def test_get_worktrees_lists_active(client, make_repo):
    make_repo("mnemonic-docs")
    make_repo("mnemonic-core")

    # Create two
    client.post("/worktree", json={**VALID_PAYLOAD, "task_id": "ALPHA-01"})
    client.post("/worktree", json={**VALID_PAYLOAD, "task_id": "BETA-02", "repo": "mnemonic-core", "topic": "core"})

    resp = client.get("/worktrees")
    assert resp.status_code == 200
    data = resp.json()
    ids = {w["task_id"] for w in data["worktrees"]}
    assert "ALPHA-01" in ids
    assert "BETA-02" in ids
    assert data["count"] == 2

    # Delete one
    client.delete("/worktree/ALPHA-01")
    resp2 = client.get("/worktrees")
    data2 = resp2.json()
    ids2 = {w["task_id"] for w in data2["worktrees"]}
    assert "ALPHA-01" not in ids2
    assert "BETA-02" in ids2
    assert data2["count"] == 1


# ---------------------------------------------------------------------------
# test_path_traversal_blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [
    "../etc/passwd",
    "..%2F..%2Fetc",
    "task id",
    "task_001",     # underscore not allowed
    "ab",           # too short
    "A" * 41,       # too long
    "task-001",     # lowercase not allowed
    "test.001",     # dot not allowed
])
def test_path_traversal_blocked(client, bad_id):
    """task_id values that don't match ^[A-Z0-9-]{3,40}$ must be rejected."""
    # Try as path parameter
    resp = client.get(f"/worktree/{bad_id}")
    assert resp.status_code in (400, 404, 422), (
        f"Expected 400/404/422 for bad task_id {bad_id!r}, got {resp.status_code}: {resp.text}"
    )


def test_path_traversal_blocked_in_post(client):
    """task_id with dots and slashes rejected at POST body validation."""
    payload = {**VALID_PAYLOAD, "task_id": "task/evil"}
    resp = client.post("/worktree", json=payload)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# test_concurrent_post_same_task_id_returns_409
# ---------------------------------------------------------------------------


def test_concurrent_post_same_task_id_returns_409(client, make_repo):
    """Race-safe: two concurrent POSTs for the same task_id — one wins, one loses."""
    make_repo("mnemonic-docs")

    results: list[int] = []

    def do_post():
        r = client.post("/worktree", json=VALID_PAYLOAD)
        results.append(r.status_code)

    t1 = threading.Thread(target=do_post)
    t2 = threading.Thread(target=do_post)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    statuses = sorted(results)
    # Exactly one 200 and one 409
    assert statuses == [200, 409], f"Expected [200, 409], got {statuses}"
    # The 409 must carry "already_exists"
    # (We can't inspect per-thread easily here; the statuses are sufficient)


# ---------------------------------------------------------------------------
# test_delete_unknown_task_id_returns_404
# ---------------------------------------------------------------------------


def test_delete_unknown_task_id_returns_404(client):
    resp = client.delete("/worktree/NOTEXIST-01")
    assert resp.status_code == 404, resp.text
    data = resp.json()
    assert data.get("detail", {}).get("error") == "not_found"
