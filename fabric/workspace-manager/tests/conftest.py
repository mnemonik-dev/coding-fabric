"""
Pytest fixtures for workspace-manager tests.

Provides:
  - tmp_settings: isolated Settings instance with temp directories
  - test_client: FastAPI TestClient wired to tmp_settings
  - mock_bw: patches vault._bw_get_item + vault._load_bw_session
  - mock_git: patches worktree._run to avoid real git calls
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure the package root is on sys.path regardless of how pytest is invoked
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings
from state import StateStore


# ---------------------------------------------------------------------------
# Settings / directory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_dirs(tmp_path: Path) -> dict[str, Path]:
    dirs = {
        "state": tmp_path / "state.json",
        "worktrees_root": tmp_path / "worktrees",
        "repos_root": tmp_path / "repos",
        "sccache_dir": tmp_path / "sccache",
        "log_file": tmp_path / "logs" / "workspace-manager.log",
        "bw_credential": tmp_path / "bw-session",
    }
    dirs["worktrees_root"].mkdir()
    dirs["repos_root"].mkdir()
    dirs["sccache_dir"].mkdir()
    dirs["log_file"].parent.mkdir(parents=True)
    # Write a dummy bw session credential
    dirs["bw_credential"].write_text("dummy-session-token\n")
    return dirs


@pytest.fixture()
def tmp_settings(tmp_dirs: dict[str, Path]) -> Settings:
    return Settings(
        bind_ip="127.0.0.1",
        bind_port=8080,
        worktrees_root=tmp_dirs["worktrees_root"],
        repos_root=tmp_dirs["repos_root"],
        sccache_dir=tmp_dirs["sccache_dir"],
        state_file=tmp_dirs["state"],
        bw_session_credential=tmp_dirs["bw_credential"],
        log_file=tmp_dirs["log_file"],
        log_level="DEBUG",
        capacity_total=10,
    )


@pytest.fixture()
def state_store(tmp_settings: Settings) -> StateStore:
    return StateStore(tmp_settings.state_file)


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------

FAKE_TOPIC_SECRETS = {
    "docs": {"BW_ITEM": "docs-secret", "ANTHROPIC_API_KEY": "sk-ant-REDACTED"},
    "core": {"BW_ITEM": "core-secret", "OPENAI_API_KEY": "sk-REDACTED"},
}


def make_bw_item(topic: str) -> dict:
    secrets = FAKE_TOPIC_SECRETS.get(topic, {"BW_DEFAULT": "default-secret"})
    notes = "\n".join(f"{k}={v}" for k, v in secrets.items())
    return {
        "id": f"fake-id-{topic}",
        "name": f"mnemonic/topic/{topic}",
        "notes": notes,
        "type": 2,
    }


@pytest.fixture()
def mock_bw(tmp_dirs: dict[str, Path]):
    """Patch vault internals to avoid real bw CLI calls.

    Both vault.check_reachable and main.check_reachable are patched because
    main.py uses `from vault import check_reachable`, binding the name in
    main's module namespace.
    """
    with (
        patch("vault._load_bw_session", return_value="dummy-session-token") as mock_session,
        patch("vault._bw_get_item", side_effect=lambda name, _session: make_bw_item(name.split("/")[-1])) as mock_item,
        patch("vault.check_reachable", return_value=True) as mock_reach_vault,
        patch("main.check_reachable", return_value=True) as mock_reach_main,
    ):
        # Expose a single handle that controls both patches simultaneously
        yield {
            "session": mock_session,
            "item": mock_item,
            "reachable_vault": mock_reach_vault,
            "reachable_main": mock_reach_main,
            # Convenience: update both at once via a helper
            "reachable": mock_reach_main,
        }


@pytest.fixture()
def mock_git(tmp_dirs: dict[str, Path]):
    """Patch worktree._run to avoid real git calls; actually creates directories."""

    def fake_run(cmd: list[str], cwd=None):
        # Simulate `git worktree add --detach <path> <ref>`
        if len(cmd) >= 4 and cmd[1] == "worktree" and cmd[2] == "add":
            path_arg = cmd[4]
            Path(path_arg).mkdir(parents=True, exist_ok=True)
        # Simulate `git worktree remove --force <path>`
        elif len(cmd) >= 4 and cmd[1] == "worktree" and cmd[2] == "remove":
            import shutil
            path_arg = cmd[4]
            try:
                shutil.rmtree(path_arg)
            except FileNotFoundError:
                pass
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    # Also ensure repo directories exist so worktree.py doesn't complain
    repos_root = tmp_dirs["repos_root"]

    with patch("worktree._run", side_effect=fake_run) as mock_run:
        yield mock_run


@pytest.fixture()
def make_repo(tmp_dirs: dict[str, Path]):
    """Helper that creates a fake repo directory in repos_root."""
    def _make(name: str) -> Path:
        p = tmp_dirs["repos_root"] / name
        p.mkdir(exist_ok=True)
        return p
    return _make


# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_settings: Settings, mock_bw, mock_git, make_repo):
    """
    FastAPI TestClient with all external dependencies mocked.
    Overrides FastAPI dependency injection for Settings and StateStore.
    """
    import main as app_module
    from fastapi.testclient import TestClient

    # Override the settings dependency
    app_module.app.dependency_overrides[app_module.get_settings] = lambda: tmp_settings

    # Rebuild the state store with tmp_settings
    store = StateStore(tmp_settings.state_file)
    app_module.app.dependency_overrides[app_module.get_state_store] = lambda: store

    with TestClient(app_module.app, raise_server_exceptions=True) as tc:
        yield tc

    app_module.app.dependency_overrides.clear()
