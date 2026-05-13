"""
Shared fixtures and test helpers for fabric.watchdog tests.

Sets WATCHDOG_REQUIRE_SANITIZER=false so the sanitizer import guard does not
hard-fail in the test environment where fabric-logs-sanitizer may not be
installed in the same venv.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow importing fabric.watchdog from the repo root.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Disable hard-fail on missing sanitizer in test environment.
os.environ.setdefault("WATCHDOG_REQUIRE_SANITIZER", "false")

import pytest


@pytest.fixture()
def tmp_state_file(tmp_path):
    """Return a temporary alerts.json path."""
    return tmp_path / "alerts.json"


@pytest.fixture()
def base_config(tmp_path):
    """Minimal watchdog config dict for tests."""
    return {
        "alert_state_file": str(tmp_path / "alerts.json"),
        "worktrees_root": str(tmp_path / "worktrees"),
        "state_file": str(tmp_path / "state.json"),
        "disk_path": str(tmp_path),
        "disk_budget_gb": 1,
    }
