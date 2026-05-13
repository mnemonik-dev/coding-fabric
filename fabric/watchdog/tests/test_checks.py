"""
Unit tests for individual check modules.

TDD anchors:
    test_orphaned_worktrees_detects_orphan
    test_disk_pressure_thresholds_75_85_90
    test_solana_rpc_unreachable_endpoint
    test_master_drift_detection
    test_failed_attestation_queue_failure
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# orphaned_worktrees
# ---------------------------------------------------------------------------


class TestOrphanedWorktrees:
    def test_orphaned_worktrees_detects_orphan(self, tmp_path):
        """A directory in worktrees_root not in state.json is an orphan."""
        from fabric.watchdog.checks.orphaned_worktrees import check

        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()
        (worktrees / "ORPHAN-001").mkdir()

        state_file = tmp_path / "state.json"
        state_file.write_text("{}")

        config = {
            "worktrees_root": str(worktrees),
            "state_file": str(state_file),
        }
        alert = check(config)
        assert alert is not None
        assert alert.alert_class == "orphaned_worktrees"
        assert "ORPHAN-001" in alert.evidence

    def test_no_orphans_when_all_tracked(self, tmp_path):
        """No alert when every directory in worktrees_root is in state.json."""
        from fabric.watchdog.checks.orphaned_worktrees import check

        worktrees = tmp_path / "worktrees"
        worktrees.mkdir()
        tracked = worktrees / "TRACKED-001"
        tracked.mkdir()

        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({
            "TRACKED-001": {
                "task_id": "TRACKED-001",
                "path": str(tracked),
            }
        }))

        config = {
            "worktrees_root": str(worktrees),
            "state_file": str(state_file),
        }
        alert = check(config)
        assert alert is None

    def test_no_alert_when_root_missing(self, tmp_path):
        """Missing worktrees_root -> no alert (not an error)."""
        from fabric.watchdog.checks.orphaned_worktrees import check

        config = {
            "worktrees_root": str(tmp_path / "nonexistent"),
            "state_file": str(tmp_path / "state.json"),
        }
        alert = check(config)
        assert alert is None

    def test_exception_inside_returns_none(self, tmp_path):
        """Internal exceptions are caught; check returns None."""
        from fabric.watchdog.checks.orphaned_worktrees import check

        # Pass a file as worktrees_root to trigger OSError inside iterdir.
        bad = tmp_path / "not_a_dir.txt"
        bad.write_text("x")
        config = {
            "worktrees_root": str(bad),
            "state_file": str(tmp_path / "state.json"),
        }
        alert = check(config)
        # bad is a file, not a dir -> os error -> returns None
        assert alert is None


# ---------------------------------------------------------------------------
# disk_pressure
# ---------------------------------------------------------------------------


class TestDiskPressure:
    """test_disk_pressure_thresholds_75_85_90"""

    def _make_statvfs(self, ratio: float):
        """Return a mock os.statvfs_result for the given used ratio."""
        mock = MagicMock()
        total_blocks = 1_000_000
        used_blocks = int(total_blocks * ratio)
        free_blocks = total_blocks - used_blocks
        mock.f_blocks = total_blocks
        mock.f_bavail = free_blocks
        mock.f_frsize = 4096
        return mock

    def test_disk_pressure_below_threshold_no_alert(self, tmp_path):
        from fabric.watchdog.checks.disk_pressure import check

        with patch("os.statvfs") as mock_stat:
            mock_stat.return_value = self._make_statvfs(0.70)
            config = {
                "disk_path": str(tmp_path),
                "disk_budget_gb": 1000,  # large budget so filesystem total is the cap
            }
            alert = check(config)
        assert alert is None

    def test_disk_pressure_thresholds_75_85_90(self, tmp_path):
        """Each threshold level triggers an alert at the right severity."""
        from fabric.watchdog.checks.disk_pressure import check
        from fabric.watchdog.models import Severity

        test_cases = [
            (0.76, Severity.WARNING),
            (0.86, Severity.WARNING),
            (0.91, Severity.CRITICAL),
        ]
        for ratio, expected_severity in test_cases:
            with patch("os.statvfs") as mock_stat:
                mock_stat.return_value = self._make_statvfs(ratio)
                config = {
                    "disk_path": str(tmp_path),
                    "disk_budget_gb": 1000,
                }
                alert = check(config)
            assert alert is not None, f"Expected alert at ratio {ratio}"
            assert alert.severity == expected_severity, f"Wrong severity at ratio {ratio}"
            assert alert.alert_class == "disk_pressure"

    def test_disk_pressure_exception_returns_none(self, tmp_path):
        from fabric.watchdog.checks.disk_pressure import check

        with patch("os.statvfs", side_effect=OSError("no such path")):
            config = {"disk_path": "/nonexistent/path", "disk_budget_gb": 50}
            alert = check(config)
        assert alert is None


# ---------------------------------------------------------------------------
# solana_rpc
# ---------------------------------------------------------------------------


class TestSolanaRpc:
    """test_solana_rpc_unreachable_endpoint"""

    def test_solana_rpc_unreachable_endpoint(self, tmp_path):
        """Alert fires when the RPC endpoint is unreachable."""
        from fabric.watchdog.checks.solana_rpc import check

        config = {
            "solana_rpc_url": "http://127.0.0.1:19999",  # nothing listening
            "solana_rpc_timeout": 1,
        }
        alert = check(config)
        assert alert is not None
        assert alert.alert_class == "solana_rpc"
        assert "unreachable" in alert.evidence.lower() or "error" in alert.evidence.lower()

    def test_solana_rpc_healthy_returns_none(self):
        """No alert when RPC responds ok."""
        import urllib.request
        from fabric.watchdog.checks.solana_rpc import check
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "result": "ok", "id": 1}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {"solana_rpc_url": "https://api.devnet.solana.com", "solana_rpc_timeout": 5}
            alert = check(config)
        assert alert is None

    def test_solana_rpc_unhealthy_result(self):
        """Alert fires when RPC returns result != 'ok'."""
        from fabric.watchdog.checks.solana_rpc import check
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "result": "behind", "id": 1}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {"solana_rpc_url": "https://api.devnet.solana.com", "solana_rpc_timeout": 5}
            alert = check(config)
        assert alert is not None
        assert "behind" in alert.evidence


# ---------------------------------------------------------------------------
# master_drift
# ---------------------------------------------------------------------------


class TestMasterDrift:
    """test_master_drift_detection"""

    def _init_git_repo(self, path: Path, branch: str = "main") -> Path:
        subprocess.run(["git", "init", "-b", branch, str(path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            check=True, capture_output=True, cwd=str(path),
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            check=True, capture_output=True, cwd=str(path),
        )
        (path / "README.md").write_text("hello")
        subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(path))
        subprocess.run(
            ["git", "commit", "-m", "init"],
            check=True, capture_output=True, cwd=str(path),
        )
        return path

    def test_master_drift_detection(self, tmp_path):
        """Alert fires when local SHA differs from origin SHA."""
        from fabric.watchdog.checks.master_drift import check

        # Create "origin" (bare) and "local" (clone) repos.
        origin = tmp_path / "origin.git"
        origin.mkdir()
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

        local = tmp_path / "local"
        subprocess.run(
            ["git", "clone", str(origin), str(local)],
            check=True, capture_output=True,
        )

        # Configure local repo identity.
        for cmd in [
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ]:
            subprocess.run(cmd, check=True, capture_output=True, cwd=str(local))

        # Create initial commit in local and push so origin has a main branch.
        (local / "README.md").write_text("initial")
        subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(local))
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            check=True, capture_output=True, cwd=str(local),
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            check=True, capture_output=True, cwd=str(local),
        )
        # Set up tracking
        subprocess.run(
            ["git", "branch", "--set-upstream-to=origin/main", "main"],
            check=True, capture_output=True, cwd=str(local),
        )

        # Now create a second commit in local but do NOT push -> drift.
        (local / "extra.md").write_text("extra")
        subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(local))
        subprocess.run(
            ["git", "commit", "-m", "extra local commit"],
            check=True, capture_output=True, cwd=str(local),
        )

        config = {
            "master_repos": [str(local)],
            "master_branch": "main",
            "git_timeout": 15,
        }
        alert = check(config)
        assert alert is not None
        assert alert.alert_class == "master_drift"

    def test_master_no_drift(self, tmp_path):
        """No alert when local is up to date with origin."""
        from fabric.watchdog.checks.master_drift import check

        origin = tmp_path / "origin.git"
        origin.mkdir()
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

        local = tmp_path / "local"
        subprocess.run(
            ["git", "clone", str(origin), str(local)],
            check=True, capture_output=True,
        )
        for cmd in [
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ]:
            subprocess.run(cmd, check=True, capture_output=True, cwd=str(local))

        (local / "README.md").write_text("initial")
        subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(local))
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            check=True, capture_output=True, cwd=str(local),
        )
        subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            check=True, capture_output=True, cwd=str(local),
        )
        subprocess.run(
            ["git", "branch", "--set-upstream-to=origin/main", "main"],
            check=True, capture_output=True, cwd=str(local),
        )

        config = {
            "master_repos": [str(local)],
            "master_branch": "main",
            "git_timeout": 15,
        }
        alert = check(config)
        assert alert is None

    def test_master_drift_missing_repo_skipped(self, tmp_path):
        """Non-existent repo path is silently skipped."""
        from fabric.watchdog.checks.master_drift import check

        config = {
            "master_repos": [str(tmp_path / "nonexistent")],
            "master_branch": "main",
        }
        alert = check(config)
        assert alert is None


# ---------------------------------------------------------------------------
# failed_attestation
# ---------------------------------------------------------------------------


class TestFailedAttestation:
    """test_failed_attestation_queue_failure"""

    def test_failed_attestation_queue_failure(self):
        """Alert fires when MCP queue contains failures."""
        from fabric.watchdog.checks.failed_attestation import check
        from unittest.mock import patch, MagicMock

        failure_payload = json.dumps([
            {"id": "attest-001", "status": "failed", "reason": "timeout"},
            {"id": "attest-002", "status": "error", "reason": "bad sig"},
        ]).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "result": {
                "content": [{"type": "text", "text": failure_payload.decode()}]
            }
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {"mnemonic_mcp_url": "http://127.0.0.1:4000", "mcp_timeout": 5}
            alert = check(config)

        assert alert is not None
        assert alert.alert_class == "failed_attestation"
        assert "2 failed" in alert.evidence

    def test_failed_attestation_no_failures(self):
        """No alert when queue is empty or all succeeded."""
        from fabric.watchdog.checks.failed_attestation import check
        from unittest.mock import patch, MagicMock

        ok_payload = json.dumps([
            {"id": "attest-003", "status": "ok"},
        ]).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "result": {
                "content": [{"type": "text", "text": ok_payload.decode()}]
            }
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {"mnemonic_mcp_url": "http://127.0.0.1:4000", "mcp_timeout": 5}
            alert = check(config)

        assert alert is None

    def test_failed_attestation_mcp_unreachable(self):
        """Alert fires (WARNING) when MCP server is unreachable."""
        from fabric.watchdog.checks.failed_attestation import check

        config = {
            "mnemonic_mcp_url": "http://127.0.0.1:19998",
            "mcp_timeout": 1,
        }
        alert = check(config)
        assert alert is not None
        assert alert.alert_class == "failed_attestation"
        from fabric.watchdog.models import Severity
        assert alert.severity == Severity.WARNING
