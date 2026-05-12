"""Tests for fabric.molyanov.validators.post_deploy_qa_wrapper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from fabric.molyanov.validators.post_deploy_qa_wrapper import DELEGATED_TO, PLUGIN, run


BROWSER_FINDINGS_FIXTURE = [
    {
        "severity": "fail",
        "category": "navigation",
        "path": "/dashboard",
        "lineno": None,
        "message": "Page load failed with HTTP 503",
        "fix": "Check service availability before deploy",
    },
    {
        "severity": "warning",
        "category": "accessibility",
        "path": "/home",
        "lineno": None,
        "message": "Missing alt text on hero image",
        "fix": "Add descriptive alt attribute",
    },
]


def _make_completed(stdout: str, returncode: int = 0) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.stderr = ""
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# test_postdeploy_invokes_browser
# ---------------------------------------------------------------------------


class TestPostDeployInvokesBrowser:
    def test_invokes_browser_plugin(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"url": "https://example.com"})
            mock_invoke.assert_called_once()
            assert mock_invoke.call_args[0][0] == PLUGIN  # "browser"

    def test_url_forwarded_as_first_arg(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"url": "https://example.com"})
            plugin_args = mock_invoke.call_args[0][1]
            assert "https://example.com" in plugin_args

    def test_scenario_forwarded(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"url": "https://example.com", "scenario": "smoke"})
            plugin_args = mock_invoke.call_args[0][1]
            assert "--scenario" in plugin_args
            assert "smoke" in plugin_args

    def test_delegated_to_is_ruflo_browser(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            result = run({})
            assert result["delegated_to"] == "ruflo:browser"


# ---------------------------------------------------------------------------
# test_normalises_to_molyanov_schema
# ---------------------------------------------------------------------------


class TestNormalisesToMolyanovSchema:
    def test_golden_output(self):
        import json

        stdout = json.dumps(BROWSER_FINDINGS_FIXTURE)
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout)
            result = run({"url": "https://example.com"})

        assert result["delegated_to"] == "ruflo:browser"
        assert len(result["findings"]) == 2
        first = result["findings"][0]
        # "fail" → "high"
        assert first["severity"] == "high"
        assert first["area"] == "navigation"

        second = result["findings"][1]
        # "warning" → "low"
        assert second["severity"] == "low"

    def test_all_finding_fields_present(self):
        import json

        stdout = json.dumps([BROWSER_FINDINGS_FIXTURE[0]])
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout)
            result = run({})

        from fabric.molyanov.validators._common import FINDING_FIELDS

        for finding in result["findings"]:
            assert set(finding.keys()) == FINDING_FIELDS


# ---------------------------------------------------------------------------
# test_plugin_missing_returns_clear_error
# ---------------------------------------------------------------------------


class TestPluginMissingReturnsClearError:
    def test_no_ruflo_binary(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo",
            side_effect=FileNotFoundError,
        ):
            result = run({"url": "https://example.com"})

        assert result["delegated_to"] == "ruflo:browser"
        finding = result["findings"][0]
        assert "ruflo" in finding["issue"].lower()
        assert "ruflo" in finding["fix_recommendation"].lower()


# ---------------------------------------------------------------------------
# test_propagates_findings
# ---------------------------------------------------------------------------


class TestPropagatesFindings:
    def test_propagates_all_findings(self):
        import json

        stdout = json.dumps(BROWSER_FINDINGS_FIXTURE)
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout, returncode=1)
            result = run({})

        assert len(result["findings"]) == 2

    def test_timeout_returns_error_finding(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo",
            side_effect=subprocess.TimeoutExpired(cmd=["ruflo"], timeout=120),
        ):
            result = run({})

        assert len(result["findings"]) == 1
        assert "timed out" in result["findings"][0]["issue"].lower()


# ---------------------------------------------------------------------------
# test_dry_run_flag
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    def test_dry_run_does_not_invoke(self):
        with patch(
            "fabric.molyanov.validators.post_deploy_qa_wrapper.invoke_ruflo"
        ) as mock_invoke:
            run({}, dry_run=True)
            mock_invoke.assert_not_called()

    def test_dry_run_returns_empty_findings(self):
        result = run({}, dry_run=True)
        assert result["findings"] == []
        assert result["delegated_to"] == "ruflo:browser"
