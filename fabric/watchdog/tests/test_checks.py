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


# ---------------------------------------------------------------------------
# stale_lkg
# ---------------------------------------------------------------------------


class TestStaleLkg:
    """Direct unit tests for stale_lkg check."""

    def _init_repo_with_tag(self, path: Path, tag_name: str, days_old: int) -> Path:
        """Create a git repo with a tag whose pointed-to commit is N days old.

        ``git log -1 --format=%ct refs/tags/<tag>`` returns the commit's author
        date, not the tag creation date.  To get a stale reading we must commit
        with a backdated GIT_AUTHOR_DATE / GIT_COMMITTER_DATE.
        """
        subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
        for cmd in [
            ["git", "config", "user.email", "t@t.com"],
            ["git", "config", "user.name", "T"],
        ]:
            subprocess.run(cmd, check=True, capture_output=True, cwd=str(path))
        (path / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(path))

        import os as _os
        env = _os.environ.copy()
        old_ts = f"{int(time.time() - days_old * 86400)} +0000"
        env["GIT_AUTHOR_DATE"] = old_ts
        env["GIT_COMMITTER_DATE"] = old_ts
        subprocess.run(
            ["git", "commit", "-m", "init"],
            check=True, capture_output=True, cwd=str(path), env=env,
        )
        subprocess.run(
            ["git", "tag", tag_name],
            check=True, capture_output=True, cwd=str(path),
        )
        return path

    def test_stale_lkg_fires_when_tag_old(self, tmp_path):
        """Alert fires when the last-known-good tag is older than stale_lkg_days."""
        from fabric.watchdog.checks.stale_lkg import check

        repo = self._init_repo_with_tag(tmp_path / "repo", "last-known-good", days_old=10)
        config = {
            "loop_repo_path": str(repo),
            "lkg_tag_name": "last-known-good",
            "stale_lkg_days": 7,
        }
        alert = check(config)
        assert alert is not None
        assert alert.alert_class == "stale_lkg"
        assert "last-known-good" in alert.evidence

    def test_stale_lkg_no_alert_when_fresh(self, tmp_path):
        """No alert when the tag is recent."""
        from fabric.watchdog.checks.stale_lkg import check

        repo = self._init_repo_with_tag(tmp_path / "repo", "last-known-good", days_old=1)
        config = {
            "loop_repo_path": str(repo),
            "lkg_tag_name": "last-known-good",
            "stale_lkg_days": 7,
        }
        alert = check(config)
        assert alert is None

    def test_stale_lkg_missing_tag(self, tmp_path):
        """Alert fires (WARNING) when the tag doesn't exist at all."""
        from fabric.watchdog.checks.stale_lkg import check

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        for cmd in [["git", "config", "user.email", "t@t.com"], ["git", "config", "user.name", "T"]]:
            subprocess.run(cmd, check=True, capture_output=True, cwd=str(repo))
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], check=True, capture_output=True, cwd=str(repo))
        subprocess.run(["git", "commit", "-m", "init"], check=True, capture_output=True, cwd=str(repo))

        config = {
            "loop_repo_path": str(repo),
            "lkg_tag_name": "last-known-good",
            "stale_lkg_days": 7,
        }
        alert = check(config)
        assert alert is not None
        assert "not found" in alert.evidence

    def test_stale_lkg_missing_repo_skipped(self, tmp_path):
        """No alert when the repo directory doesn't exist."""
        from fabric.watchdog.checks.stale_lkg import check

        config = {"loop_repo_path": str(tmp_path / "nonexistent")}
        alert = check(config)
        assert alert is None


# ---------------------------------------------------------------------------
# irys_balance
# ---------------------------------------------------------------------------


class TestIrysBalance:
    """Direct unit tests for irys_balance check."""

    def test_irys_balance_below_threshold_fires(self):
        """Alert fires when balance is below the minimum threshold."""
        from fabric.watchdog.checks.irys_balance import check
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"balance": "500000"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {
                "irys_node_url": "https://devnet.irys.xyz",
                "irys_address": "walletABCDEF",
                "irys_balance_min": 1_000_000,
                "irys_rpc_timeout": 5,
            }
            alert = check(config)

        assert alert is not None
        assert alert.alert_class == "irys_balance"
        assert "500000" in alert.evidence

    def test_irys_balance_sufficient_returns_none(self):
        """No alert when balance is at or above the minimum."""
        from fabric.watchdog.checks.irys_balance import check
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"balance": "2000000"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {
                "irys_node_url": "https://devnet.irys.xyz",
                "irys_address": "walletABCDEF",
                "irys_balance_min": 1_000_000,
            }
            alert = check(config)

        assert alert is None

    def test_irys_balance_unreachable_fires(self):
        """Alert fires (WARNING) when Irys node is unreachable."""
        from fabric.watchdog.checks.irys_balance import check

        config = {
            "irys_node_url": "https://devnet.irys.xyz",
            "irys_address": "walletXYZ",
            "irys_rpc_timeout": 0.01,  # near-zero timeout to force failure
        }
        # We can't guarantee a connection error with a real hostname in CI,
        # so mock the network layer.
        import urllib.error
        from unittest.mock import patch
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            alert = check(config)

        assert alert is not None
        assert alert.alert_class == "irys_balance"
        from fabric.watchdog.models import Severity
        assert alert.severity == Severity.WARNING

    def test_irys_balance_no_address_skipped(self):
        """No alert when irys_address is not configured."""
        from fabric.watchdog.checks.irys_balance import check

        config = {"irys_node_url": "https://devnet.irys.xyz"}
        alert = check(config)
        assert alert is None

    def test_irys_balance_deterministic_id(self):
        """Same address+node always produces the same alert_id."""
        from fabric.watchdog.checks.irys_balance import check
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"balance": "0"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            config = {
                "irys_node_url": "https://devnet.irys.xyz",
                "irys_address": "walletSTABLE",
                "irys_balance_min": 1_000_000,
            }
            a1 = check(config)
            a2 = check(config)

        assert a1 is not None and a2 is not None
        assert a1.alert_id == a2.alert_id


# ---------------------------------------------------------------------------
# Security: SSRF / URL allowlist
# ---------------------------------------------------------------------------


class TestSSRFUrlAllowlist:
    """SSRF prevention: only allowlisted schemes and hostnames are permitted."""

    def test_file_scheme_rejected(self):
        from fabric.watchdog.url_validator import validate_url
        with pytest.raises(ValueError, match="not allowed"):
            validate_url("file:///etc/passwd", context="test")

    def test_ftp_scheme_rejected(self):
        from fabric.watchdog.url_validator import validate_url
        with pytest.raises(ValueError, match="not allowed"):
            validate_url("ftp://example.com/data", context="test")

    def test_loopback_always_allowed(self):
        from fabric.watchdog.url_validator import validate_url
        # Should not raise
        validate_url("http://127.0.0.1:4000/mcp", context="test")
        validate_url("http://localhost:3000", context="test")

    def test_private_ip_allowed(self):
        from fabric.watchdog.url_validator import validate_url
        # Tailnet/private ranges are fine
        validate_url("http://100.64.1.1:8080/health", context="test")

    def test_unknown_public_host_rejected(self):
        from fabric.watchdog.url_validator import validate_url
        with pytest.raises(ValueError, match="not in the watchdog URL allowlist"):
            validate_url("https://mainnet-beta.solana.com", context="test")

    def test_allowlisted_public_host_passes(self):
        from fabric.watchdog.url_validator import validate_url
        validate_url("https://api.devnet.solana.com", context="test")
        validate_url("https://devnet.irys.xyz", context="test")
        validate_url("https://api.telegram.org", context="test")

    def test_solana_check_rejects_mainnet_url(self, tmp_path):
        """solana_rpc check returns None (via exception swallow) for mainnet URL."""
        from fabric.watchdog.checks.solana_rpc import check

        config = {
            "solana_rpc_url": "https://mainnet-beta.solana.com",
            "solana_rpc_timeout": 1,
        }
        # validate_url raises ValueError; outer check() catches and returns None.
        alert = check(config)
        assert alert is None

    def test_irys_check_rejects_file_url(self, tmp_path):
        """irys_balance check returns None for file:// URL."""
        from fabric.watchdog.checks.irys_balance import check

        config = {
            "irys_node_url": "file:///etc/passwd",
            "irys_address": "walletABC",
        }
        alert = check(config)
        assert alert is None


# ---------------------------------------------------------------------------
# hung_tmux — direct detection tests
# ---------------------------------------------------------------------------


class TestHungTmux:
    """Direct unit tests for the hung_tmux check (AC30 last gap).

    Covers: tmux binary missing → None; fresh session → None; stale session
    over threshold → WARNING alert with session name and idle hours.
    """

    def test_hung_tmux_missing_binary_returns_none(self):
        """When tmux is not installed, check returns None gracefully."""
        from fabric.watchdog.checks.hung_tmux import check

        with patch("fabric.watchdog.checks.hung_tmux.shutil.which", return_value=None):
            alert = check({})
        assert alert is None

    def test_hung_tmux_fresh_session_no_alert(self):
        """Session whose last activity is well under threshold → no alert."""
        from fabric.watchdog.checks.hung_tmux import check

        now = int(time.time())
        recent_activity = now - 60  # 1 minute ago
        fake_stdout = f"work {recent_activity}\nbuild {recent_activity - 10}\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_stdout

        with patch(
            "fabric.watchdog.checks.hung_tmux.shutil.which", return_value="/usr/bin/tmux"
        ), patch(
            "fabric.watchdog.checks.hung_tmux.subprocess.run", return_value=mock_result
        ):
            alert = check({"tmux_idle_hours": 12})

        assert alert is None

    def test_hung_tmux_stale_session_emits_warning(self):
        """Session idle > threshold yields a WARNING alert listing the session."""
        from fabric.watchdog.checks.hung_tmux import check
        from fabric.watchdog.models import Severity

        now = int(time.time())
        # 20 hours idle, threshold 12h
        stale_activity = now - (20 * 3600)
        fake_stdout = f"stale-task {stale_activity}\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = fake_stdout

        with patch(
            "fabric.watchdog.checks.hung_tmux.shutil.which", return_value="/usr/bin/tmux"
        ), patch(
            "fabric.watchdog.checks.hung_tmux.subprocess.run", return_value=mock_result
        ):
            alert = check({"tmux_idle_hours": 12})

        assert alert is not None
        assert alert.alert_class == "hung_tmux"
        assert alert.severity == Severity.WARNING
        assert "stale-task" in alert.evidence
        # Idle hours ~20.0; allow any format including the substring "20"
        assert "20" in alert.evidence or "19.9" in alert.evidence

    def test_hung_tmux_no_sessions_running(self):
        """tmux returncode != 0 (no sessions running) → returns None, no error."""
        from fabric.watchdog.checks.hung_tmux import check

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch(
            "fabric.watchdog.checks.hung_tmux.shutil.which", return_value="/usr/bin/tmux"
        ), patch(
            "fabric.watchdog.checks.hung_tmux.subprocess.run", return_value=mock_result
        ):
            alert = check({})

        assert alert is None


# ---------------------------------------------------------------------------
# Security: disabled_checks guard
# ---------------------------------------------------------------------------


class TestDisabledChecksGuard:
    """Disabling a critical-class check without acknowledgement is rejected."""

    def test_critical_check_not_disabled_without_ack(self, tmp_path):
        """failed_attestation cannot be silently disabled."""
        from fabric.watchdog.scheduler import _run_check

        config = {
            "disabled_checks": ["failed_attestation"],
            # acknowledge_disable_consequences deliberately absent
        }
        # The check would contact the real network, so patch it to be safe.
        with patch("importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_mod.check.return_value = None
            mock_import.return_value = mock_mod
            result = _run_check("fabric.watchdog.checks.failed_attestation", config)
        # Should have run (not skipped) because ack was missing.
        mock_mod.check.assert_called_once()

    def test_critical_check_disabled_with_ack(self, tmp_path):
        """failed_attestation is skipped when acknowledge_disable_consequences=true."""
        from fabric.watchdog.scheduler import _run_check

        config = {
            "disabled_checks": ["failed_attestation"],
            "acknowledge_disable_consequences": True,
        }
        result = _run_check("fabric.watchdog.checks.failed_attestation", config)
        assert result is None

    def test_non_critical_check_disabled_normally(self, tmp_path):
        """Non-critical checks are disabled without requiring acknowledgement."""
        from fabric.watchdog.scheduler import _run_check

        config = {"disabled_checks": ["hung_tmux"]}
        result = _run_check("fabric.watchdog.checks.hung_tmux", config)
        assert result is None


# ---------------------------------------------------------------------------
# Security: token scrubbing
# ---------------------------------------------------------------------------


class TestTokenScrubbing:
    """Telegram bot token must not leak into log output."""

    def test_scrub_token_removes_token(self):
        from fabric.watchdog.telegram import _scrub_token

        token = "bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
        url = f"https://api.telegram.org/{token}/sendMessage"
        result = _scrub_token(url)
        assert "123456789" not in result
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi" not in result
        assert "REDACTED" in result

    def test_send_failure_logs_sanitized_message(self, tmp_path):
        """When sendMessage fails, the logged message must not contain the token."""
        import logging
        import urllib.error
        from unittest.mock import patch

        from fabric.watchdog.telegram import TelegramPoster

        log_records = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                log_records.append(self.format(record))

        handler = CapturingHandler()
        tg_logger = logging.getLogger("fabric.watchdog.telegram")
        tg_logger.addHandler(handler)
        try:
            poster = TelegramPoster(
                bot_token="987654321:ZYXWVUTSRQPONMLKJIHGFEDCBAzyxwvutsr",
                chat_id="-100123456",
            )
            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("connection refused")):
                poster._send("test message")
        finally:
            tg_logger.removeHandler(handler)

        combined = " ".join(log_records)
        assert "987654321" not in combined
        assert "ZYXWVUTSRQPONMLKJIHGFEDCBAzyxwvutsr" not in combined


# ---------------------------------------------------------------------------
# State file permissions
# ---------------------------------------------------------------------------


class TestStateFileMode:
    """State file must be written with mode 0640."""

    def test_state_file_mode_0640(self, tmp_path):
        """alerts.json is created with mode 0640."""
        import stat
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        store.mark_seen("test-id-mode")

        mode = oct(stat.S_IMODE(state_file.stat().st_mode))
        assert mode == oct(0o640), f"Expected 0640, got {mode}"

    def test_state_file_mode_preserved_after_update(self, tmp_path):
        """Mode remains 0640 after mark_seen updates the file."""
        import stat
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        store.mark_seen("id-one")
        store.mark_seen("id-two")

        mode = oct(stat.S_IMODE(state_file.stat().st_mode))
        assert mode == oct(0o640), f"Expected 0640 after update, got {mode}"


# ---------------------------------------------------------------------------
# Alert state: alert_class binding
# ---------------------------------------------------------------------------


class TestAlertClassBinding:
    """alert_class is stored and checked to prevent mismatched /turn-into-task."""

    def test_mark_seen_stores_alert_class(self, tmp_path):
        import json
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        store.mark_seen("bound-id", alert_class="disk_pressure")

        raw = json.loads(state_file.read_text())
        assert raw["bound-id"]["alert_class"] == "disk_pressure"

    def test_check_seen_with_class_passes_correct(self, tmp_path):
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        store.mark_seen("id-x", alert_class="solana_rpc")

        assert store.check_seen_with_class("id-x", "solana_rpc") is True

    def test_check_seen_with_class_rejects_mismatch(self, tmp_path):
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        store.mark_seen("id-y", alert_class="solana_rpc")

        assert store.check_seen_with_class("id-y", "disk_pressure") is False

    def test_get_alert_class_returns_stored_class(self, tmp_path):
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        store.mark_seen("id-z", alert_class="failed_attestation")

        assert store.get_alert_class("id-z") == "failed_attestation"

    def test_get_alert_class_returns_none_for_unknown(self, tmp_path):
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)

        assert store.get_alert_class("nonexistent") is None


# ---------------------------------------------------------------------------
# Deterministic alert_id
# ---------------------------------------------------------------------------


class TestDeterministicAlertId:
    """deterministic_alert_id produces stable ids for same inputs."""

    def test_same_input_same_id(self):
        from fabric.watchdog.alert_state import deterministic_alert_id

        a = deterministic_alert_id("solana_rpc", "https://api.devnet.solana.com")
        b = deterministic_alert_id("solana_rpc", "https://api.devnet.solana.com")
        assert a == b

    def test_different_class_different_id(self):
        from fabric.watchdog.alert_state import deterministic_alert_id

        a = deterministic_alert_id("solana_rpc", "sig")
        b = deterministic_alert_id("irys_balance", "sig")
        assert a != b

    def test_different_sig_different_id(self):
        from fabric.watchdog.alert_state import deterministic_alert_id

        a = deterministic_alert_id("solana_rpc", "sig1")
        b = deterministic_alert_id("solana_rpc", "sig2")
        assert a != b

    def test_returns_64_char_hex(self):
        from fabric.watchdog.alert_state import deterministic_alert_id
        import re

        result = deterministic_alert_id("test", "data")
        assert len(result) == 64
        assert re.match(r"^[0-9a-f]{64}$", result)
