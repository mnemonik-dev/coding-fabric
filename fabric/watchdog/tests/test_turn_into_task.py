"""
Unit tests for turn_into_task command handler.

TDD anchors:
    test_turn_into_task_creates_kaneo_card
    test_unknown_alert_id_returns_friendly_error
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fabric.watchdog.models import Alert, Severity


class TestTurnIntoTask:
    """test_turn_into_task_creates_kaneo_card"""

    def test_turn_into_task_creates_kaneo_card(self, tmp_path):
        """Known alert_id results in a Kaneo card being created."""
        from fabric.watchdog.turn_into_task import handle_command
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)

        alert = Alert(
            alert_id="11111111-0000-0000-0000-000000000001",
            alert_class="disk_pressure",
            severity=Severity.WARNING,
            evidence="disk at 85%",
        )
        store.mark_seen(alert.alert_id)

        mock_kaneo = MagicMock()
        mock_kaneo.create_card.return_value = {"id": "card-42", "title": "[WARNING] disk_pressure"}

        replies = []
        config = {"alert_state_file": str(state_file)}

        handle_command(
            text=f"/turn-into-task {alert.alert_id}",
            config=config,
            reply=replies.append,
            alert_store=store,
            kaneo_client=mock_kaneo,
            alert_registry={alert.alert_id: alert},
        )

        mock_kaneo.create_card.assert_called_once_with(alert)
        assert len(replies) == 1
        assert "card-42" in replies[0]

    def test_unknown_alert_id_returns_friendly_error(self, tmp_path):
        """test_unknown_alert_id_returns_friendly_error"""
        from fabric.watchdog.turn_into_task import handle_command
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)

        mock_kaneo = MagicMock()
        replies = []
        config = {"alert_state_file": str(state_file)}

        handle_command(
            text="/turn-into-task 00000000-0000-0000-0000-000000000000",
            config=config,
            reply=replies.append,
            alert_store=store,
            kaneo_client=mock_kaneo,
        )

        mock_kaneo.create_card.assert_not_called()
        assert len(replies) == 1
        assert "Unknown or expired" in replies[0] or "unknown" in replies[0].lower()

    def test_missing_alert_id_shows_usage(self, tmp_path):
        """No alert_id argument returns usage string."""
        from fabric.watchdog.turn_into_task import handle_command

        replies = []
        handle_command(
            text="/turn-into-task",
            config={},
            reply=replies.append,
        )
        assert len(replies) == 1
        assert "Usage" in replies[0]

    def test_kaneo_failure_returns_error_message(self, tmp_path):
        """If Kaneo returns None, user gets an error message."""
        from fabric.watchdog.turn_into_task import handle_command
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)

        alert = Alert(
            alert_id="22222222-0000-0000-0000-000000000002",
            alert_class="solana_rpc",
            severity=Severity.CRITICAL,
            evidence="RPC down",
        )
        store.mark_seen(alert.alert_id)

        mock_kaneo = MagicMock()
        mock_kaneo.create_card.return_value = None  # simulate failure

        replies = []
        config = {"alert_state_file": str(state_file)}

        handle_command(
            text=f"/turn-into-task {alert.alert_id}",
            config=config,
            reply=replies.append,
            alert_store=store,
            kaneo_client=mock_kaneo,
            alert_registry={alert.alert_id: alert},
        )

        assert len(replies) == 1
        assert "Failed" in replies[0]

    def test_placeholder_alert_when_registry_missing(self, tmp_path):
        """When alert not in registry but in state, a placeholder card is created."""
        from fabric.watchdog.turn_into_task import handle_command
        from fabric.watchdog.alert_state import AlertStateStore

        state_file = tmp_path / "alerts.json"
        store = AlertStateStore(state_file)
        alert_id = "33333333-0000-0000-0000-000000000003"
        store.mark_seen(alert_id)

        mock_kaneo = MagicMock()
        mock_kaneo.create_card.return_value = {"id": "card-99", "title": "placeholder"}

        replies = []
        config = {"alert_state_file": str(state_file)}

        handle_command(
            text=f"/turn-into-task {alert_id}",
            config=config,
            reply=replies.append,
            alert_store=store,
            kaneo_client=mock_kaneo,
            alert_registry={},  # empty registry
        )

        mock_kaneo.create_card.assert_called_once()
        assert "card-99" in replies[0]
