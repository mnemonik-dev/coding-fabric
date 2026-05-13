"""
Tests for pagination handling (stale_prs) and Telegram rate-limiting.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call
from io import BytesIO

import pytest


class TestStaleprsPagination:
    """Verify stale_prs follows GitHub pagination via Link headers."""

    def test_pagination_follows_next_link(self):
        """stale_prs fetches all pages when Link: rel=next is present."""
        from fabric.watchdog.checks.stale_prs import _gh_get, _parse_next_link

        page1 = [{"number": 1, "title": "old PR", "created_at": "2020-01-01T00:00:00Z"}]
        page2 = [{"number": 2, "title": "another old PR", "created_at": "2020-02-01T00:00:00Z"}]

        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            mock = MagicMock()
            import json
            if call_count[0] == 1:
                mock.read.return_value = json.dumps(page1).encode()
                mock.headers = {"Link": '<https://api.github.com/page2>; rel="next"'}
            else:
                mock.read.return_value = json.dumps(page2).encode()
                mock.headers = {}
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            items = _gh_get("https://api.github.com/page1", token="", timeout=5)

        assert len(items) == 2
        assert call_count[0] == 2

    def test_parse_next_link_extracts_url(self):
        from fabric.watchdog.checks.stale_prs import _parse_next_link

        header = '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="next", <https://api.github.com/repos/org/repo/pulls?page=5>; rel="last"'
        url = _parse_next_link(header)
        assert url == "https://api.github.com/repos/org/repo/pulls?page=2"

    def test_parse_next_link_no_next_returns_none(self):
        from fabric.watchdog.checks.stale_prs import _parse_next_link

        header = '<https://api.github.com/repos/org/repo/pulls?page=5>; rel="last"'
        assert _parse_next_link(header) is None

    def test_parse_next_link_empty_returns_none(self):
        from fabric.watchdog.checks.stale_prs import _parse_next_link

        assert _parse_next_link("") is None


class TestTelegramRateLimit:
    """Verify TelegramPoster throttles to <= 20 msg/s."""

    def test_rate_limit_enforced(self):
        """Sending many messages in rapid succession respects the 50ms interval."""
        from fabric.watchdog.telegram import TelegramPoster, _MIN_INTERVAL

        poster = TelegramPoster(
            bot_token="test:token",
            chat_id="-1001234567890",
        )

        send_times = []

        def fake_urlopen(req, timeout=None):
            send_times.append(time.monotonic())
            mock = MagicMock()
            import json
            mock.read.return_value = json.dumps({"ok": True, "result": {}}).encode()
            mock.__enter__ = lambda s: s
            mock.__exit__ = MagicMock(return_value=False)
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            from fabric.watchdog.models import Alert, Severity
            alerts = [
                Alert(
                    alert_class=f"check_{i}",
                    severity=Severity.WARNING,
                    evidence=f"test {i}",
                )
                for i in range(5)
            ]
            for alert in alerts:
                poster.post_alert(alert)

        assert len(send_times) == 5
        for i in range(1, len(send_times)):
            gap = send_times[i] - send_times[i - 1]
            # Allow 5ms tolerance for test execution jitter.
            assert gap >= _MIN_INTERVAL - 0.005, (
                f"Rate limit violated: gap {gap*1000:.1f}ms < {_MIN_INTERVAL*1000:.1f}ms "
                f"between msg {i-1} and {i}"
            )
