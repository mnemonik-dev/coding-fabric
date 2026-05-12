"""Tests for fabric.molyanov.validators.security_auditor_wrapper."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from fabric.molyanov.validators.security_auditor_wrapper import DELEGATED_TO, PLUGIN, run


SECURITY_AUDIT_FIXTURE = [
    {
        "severity": "critical",
        "category": "injection",
        "path": "src/api.py",
        "lineno": 30,
        "message": "Unsanitised user input passed to eval()",
        "fix": "Validate and escape all user-controlled input",
    },
    {
        "severity": "medium",
        "category": "auth",
        "path": "src/api.py",
        "lineno": 45,
        "message": "Missing authentication check",
        "fix": "Add decorator @require_auth",
    },
]


def _make_completed(stdout: str, returncode: int = 0) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.stderr = ""
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# test_security_invokes_security_audit
# ---------------------------------------------------------------------------


class TestSecurityInvokesSecurityAudit:
    def test_invokes_security_audit_plugin(self):
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"target": "src/"})
            mock_invoke.assert_called_once()
            assert mock_invoke.call_args[0][0] == PLUGIN  # "security-audit"

    def test_target_forwarded(self):
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"target": "src/api.py"})
            plugin_args = mock_invoke.call_args[0][1]
            assert "src/api.py" in plugin_args

    def test_profile_forwarded(self):
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"target": "src/", "profile": "owasp"})
            plugin_args = mock_invoke.call_args[0][1]
            assert "--profile" in plugin_args
            assert "owasp" in plugin_args

    def test_delegated_to_is_ruflo_security_audit(self):
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            result = run({})
            assert result["delegated_to"] == "ruflo:security-audit"


# ---------------------------------------------------------------------------
# test_normalises_to_molyanov_schema
# ---------------------------------------------------------------------------


class TestNormalisesToMolyanovSchema:
    def test_golden_output(self):
        import json

        stdout = json.dumps(SECURITY_AUDIT_FIXTURE)
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout)
            result = run({"target": "src/api.py"})

        assert result["delegated_to"] == "ruflo:security-audit"
        assert len(result["findings"]) == 2
        first = result["findings"][0]
        assert first["severity"] == "critical"
        assert first["area"] == "injection"
        assert first["line"] == 30

    def test_all_finding_fields_present(self):
        import json

        stdout = json.dumps([SECURITY_AUDIT_FIXTURE[0]])
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
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
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo",
            side_effect=FileNotFoundError,
        ):
            result = run({"target": "src/"})

        assert result["delegated_to"] == "ruflo:security-audit"
        finding = result["findings"][0]
        assert "ruflo" in finding["issue"].lower()
        assert "ruflo" in finding["fix_recommendation"].lower()


# ---------------------------------------------------------------------------
# test_propagates_findings
# ---------------------------------------------------------------------------


class TestPropagatesFindings:
    def test_propagates_all_findings(self):
        import json

        stdout = json.dumps(SECURITY_AUDIT_FIXTURE)
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout, returncode=1)
            result = run({})

        assert len(result["findings"]) == 2


# ---------------------------------------------------------------------------
# test_dry_run_flag
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    def test_dry_run_does_not_invoke(self):
        with patch(
            "fabric.molyanov.validators.security_auditor_wrapper.invoke_ruflo"
        ) as mock_invoke:
            run({}, dry_run=True)
            mock_invoke.assert_not_called()

    def test_dry_run_returns_empty_findings(self):
        result = run({}, dry_run=True)
        assert result["findings"] == []
        assert result["delegated_to"] == "ruflo:security-audit"
