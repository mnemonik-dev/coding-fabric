"""
Unit tests for the scheduler.

TDD anchors:
    test_batches_when_more_than_3_alerts_fire
    test_per_check_timeout
    test_alert_state_persistence (24h)
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fabric.watchdog.models import Alert, Severity
from fabric.watchdog.scheduler import Scheduler, _BATCH_THRESHOLD


def _make_alert(alert_class: str = "test_check", severity: Severity = Severity.WARNING) -> Alert:
    return Alert(
        alert_class=alert_class,
        severity=severity,
        evidence=f"synthetic {alert_class} alert",
    )


class TestSchedulerBatching:
    """test_batches_when_more_than_3_alerts_fire"""

    def test_batches_when_more_than_3_alerts_fire(self, tmp_path):
        """When >3 distinct alerts fire, they are sent as a single digest."""
        alerts = [_make_alert(f"check_{i}") for i in range(5)]

        mock_telegram = MagicMock()
        mock_telegram.post_digest.return_value = True

        config = {"alert_state_file": str(tmp_path / "alerts.json")}
        scheduler = Scheduler(config, telegram=mock_telegram, kaneo=MagicMock())

        with patch.object(scheduler, "_run_all_checks", return_value=alerts):
            new = scheduler.tick()

        assert len(new) == 5
        mock_telegram.post_digest.assert_called_once()
        mock_telegram.post_alert.assert_not_called()

    def test_individual_send_when_3_or_fewer(self, tmp_path):
        """When <=3 alerts, each is sent individually."""
        alerts = [_make_alert(f"check_{i}") for i in range(3)]

        mock_telegram = MagicMock()
        mock_telegram.post_alert.return_value = True

        config = {"alert_state_file": str(tmp_path / "alerts.json")}
        scheduler = Scheduler(config, telegram=mock_telegram, kaneo=MagicMock())

        with patch.object(scheduler, "_run_all_checks", return_value=alerts):
            new = scheduler.tick()

        assert len(new) == 3
        assert mock_telegram.post_alert.call_count == 3
        mock_telegram.post_digest.assert_not_called()

    def test_no_dispatch_when_no_new_alerts(self, tmp_path):
        """No dispatch when all alerts are already in the 24h cache."""
        alert = _make_alert()

        mock_telegram = MagicMock()
        config = {"alert_state_file": str(tmp_path / "alerts.json")}
        scheduler = Scheduler(config, telegram=mock_telegram, kaneo=MagicMock())

        # Seed the state so the alert is already seen.
        scheduler._state.mark_seen(alert.alert_id)

        with patch.object(scheduler, "_run_all_checks", return_value=[alert]):
            new = scheduler.tick()

        assert new == []
        mock_telegram.post_alert.assert_not_called()
        mock_telegram.post_digest.assert_not_called()


class TestSchedulerTimeout:
    """test_per_check_timeout"""

    def test_per_check_timeout(self, tmp_path):
        """A hanging check times out within _PER_CHECK_TIMEOUT and does not block other checks."""
        import concurrent.futures
        from fabric.watchdog import scheduler as sched_module

        fast_alert = _make_alert("fast_check")

        # Use a very short timeout for the test to avoid 30s waits.
        SHORT_TIMEOUT = 1  # second

        original_timeout = sched_module._PER_CHECK_TIMEOUT

        def patched_run_check(module_name, config):
            if "hung_tmux" in module_name:
                # This check sleeps much longer than the patched timeout.
                time.sleep(SHORT_TIMEOUT * 10)
                return _make_alert("slow_check")
            return fast_alert

        mock_telegram = MagicMock()
        mock_telegram.post_alert.return_value = True
        config = {"alert_state_file": str(tmp_path / "alerts.json")}
        scheduler = Scheduler(config, telegram=mock_telegram, kaneo=MagicMock())

        fast_mod = "fabric.watchdog.checks.orphaned_worktrees"
        slow_mod = "fabric.watchdog.checks.hung_tmux"

        try:
            sched_module._PER_CHECK_TIMEOUT = SHORT_TIMEOUT
            with patch("fabric.watchdog.scheduler._CHECK_MODULES", [fast_mod, slow_mod]):
                with patch("fabric.watchdog.scheduler._run_check", side_effect=patched_run_check):
                    start = time.monotonic()
                    new = scheduler.tick()
                    elapsed = time.monotonic() - start
        finally:
            sched_module._PER_CHECK_TIMEOUT = original_timeout

        # The scheduler should complete in approximately SHORT_TIMEOUT (for the
        # slow check) + epsilon for the fast check, not SHORT_TIMEOUT * 10.
        # Allow 3x for scheduling / CI jitter.
        assert elapsed < SHORT_TIMEOUT * 3 + 2, (
            f"Scheduler took {elapsed:.2f}s — slow check did not time out promptly"
        )
        # The fast check result should be collected.
        assert mock_telegram.post_alert.call_count == 1


class TestAlertStatePersistence:
    """test_alert_state_persistence (24h)"""

    def test_alert_state_persists_across_instances(self, tmp_path):
        """Seen alert_id persists in the JSON file and is read by a new instance."""
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store1 = AlertStateStore(state_file)
        store1.mark_seen("test-alert-abc")

        # Second instance reads the same file.
        store2 = AlertStateStore(state_file)
        assert store2.is_seen("test-alert-abc")

    def test_alert_state_expires_after_ttl(self, tmp_path):
        """An entry with recorded_at > 24h ago is treated as unseen."""
        import json
        from fabric.watchdog.alert_state import AlertStateStore, _TTL_SECONDS

        state_file = tmp_path / "alerts.json"
        # Write an expired entry directly.
        expired_ts = time.time() - _TTL_SECONDS - 10
        state_file.write_text(json.dumps({"old-alert": {"recorded_at": expired_ts}}))

        store = AlertStateStore(state_file)
        assert not store.is_seen("old-alert")

    def test_clear_expired_removes_old_entries(self, tmp_path):
        """clear_expired() removes entries past 24h."""
        import json
        from fabric.watchdog.alert_state import AlertStateStore, _TTL_SECONDS

        state_file = tmp_path / "alerts.json"
        expired_ts = time.time() - _TTL_SECONDS - 10
        fresh_ts = time.time() - 60

        state_file.write_text(json.dumps({
            "old-alert": {"recorded_at": expired_ts},
            "new-alert": {"recorded_at": fresh_ts},
        }))

        store = AlertStateStore(state_file)
        removed = store.clear_expired()
        assert removed == 1
        assert store.is_seen("new-alert")
        assert not store.is_seen("old-alert")

    def test_scheduler_deduplicates_within_24h(self, tmp_path):
        """Same alert_id fired on two consecutive ticks only dispatches once."""
        alert = _make_alert("orphaned_worktrees")

        mock_telegram = MagicMock()
        mock_telegram.post_alert.return_value = True
        config = {"alert_state_file": str(tmp_path / "alerts.json")}
        scheduler = Scheduler(config, telegram=mock_telegram, kaneo=MagicMock())

        with patch.object(scheduler, "_run_all_checks", return_value=[alert]):
            first = scheduler.tick()
            second = scheduler.tick()

        assert len(first) == 1
        assert len(second) == 0
        assert mock_telegram.post_alert.call_count == 1
