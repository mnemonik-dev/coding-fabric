"""Tests for fabric.molyanov.validators._common shared utilities."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from fabric.molyanov.validators._common import (
    FINDING_FIELDS,
    canonicalize_finding,
    error_result,
    invoke_ruflo,
    normalise_severity,
    parse_plugin_output,
)


# ---------------------------------------------------------------------------
# normalise_severity
# ---------------------------------------------------------------------------


class TestNormaliseSeverity:
    def test_canonical_passthrough(self):
        for level in ("critical", "high", "medium", "low", "info"):
            assert normalise_severity(level) == level

    def test_aliases_mapped(self):
        assert normalise_severity("blocker") == "critical"
        assert normalise_severity("major") == "high"
        assert normalise_severity("warning") == "low"
        assert normalise_severity("informational") == "info"
        assert normalise_severity("med") == "medium"

    def test_case_insensitive(self):
        assert normalise_severity("HIGH") == "high"
        assert normalise_severity("Critical") == "critical"

    def test_unknown_returns_info(self):
        assert normalise_severity("totally-unknown-level") == "info"

    def test_non_string_returns_info(self):
        assert normalise_severity(None) == "info"
        assert normalise_severity(42) == "info"
        assert normalise_severity([]) == "info"


# ---------------------------------------------------------------------------
# canonicalize_finding
# ---------------------------------------------------------------------------


class TestCanonicalizeFinding:
    def test_canonical_fields_passthrough(self):
        raw = {
            "severity": "high",
            "area": "security",
            "file": "src/main.py",
            "line": 42,
            "issue": "SQL injection",
            "fix_recommendation": "Use parameterised queries",
        }
        out = canonicalize_finding(raw)
        assert set(out.keys()) == FINDING_FIELDS
        assert out["severity"] == "high"
        assert out["area"] == "security"
        assert out["file"] == "src/main.py"
        assert out["line"] == 42
        assert out["issue"] == "SQL injection"

    def test_field_aliases_translated(self):
        raw = {
            "category": "auth",
            "path": "app/auth.py",
            "lineno": "10",
            "message": "Weak password hashing",
            "fix": "Use bcrypt",
            "severity": "critical",
        }
        out = canonicalize_finding(raw)
        assert out["area"] == "auth"
        assert out["file"] == "app/auth.py"
        assert out["line"] == 10
        assert out["issue"] == "Weak password hashing"
        assert out["fix_recommendation"] == "Use bcrypt"

    def test_unknown_fields_discarded(self):
        raw = {"severity": "low", "extra_field": "should be gone"}
        out = canonicalize_finding(raw)
        assert "extra_field" not in out
        assert set(out.keys()) == FINDING_FIELDS

    def test_missing_fields_get_defaults(self):
        out = canonicalize_finding({})
        assert out["severity"] == "info"
        assert out["area"] == "general"
        assert out["file"] == ""
        assert out["line"] is None
        assert out["issue"] == ""
        assert out["fix_recommendation"] == ""

    def test_line_coerced_to_int(self):
        out = canonicalize_finding({"line": "55"})
        assert out["line"] == 55

    def test_line_none_when_absent(self):
        out = canonicalize_finding({})
        assert out["line"] is None

    def test_severity_normalised(self):
        out = canonicalize_finding({"severity": "ERROR"})
        assert out["severity"] == "high"


# ---------------------------------------------------------------------------
# parse_plugin_output
# ---------------------------------------------------------------------------


class TestParsePluginOutput:
    DELEGATED_TO = "ruflo:test-plugin"

    def test_array_output(self):
        stdout = '[{"severity": "high", "message": "Issue A", "category": "auth"}]'
        result = parse_plugin_output(stdout, self.DELEGATED_TO)
        assert result["delegated_to"] == self.DELEGATED_TO
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "high"
        assert result["findings"][0]["issue"] == "Issue A"

    def test_object_with_findings_key(self):
        stdout = '{"findings": [{"severity": "low", "message": "Minor"}], "meta": "x"}'
        result = parse_plugin_output(stdout, self.DELEGATED_TO)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "low"

    def test_empty_stdout_returns_empty_findings(self):
        result = parse_plugin_output("", self.DELEGATED_TO)
        assert result["findings"] == []
        assert result["delegated_to"] == self.DELEGATED_TO

    def test_invalid_json_returns_empty_findings(self):
        result = parse_plugin_output("not json {{", self.DELEGATED_TO)
        assert result["findings"] == []

    def test_non_dict_items_skipped(self):
        stdout = '[{"severity": "high", "message": "ok"}, null, "string", 42]'
        result = parse_plugin_output(stdout, self.DELEGATED_TO)
        assert len(result["findings"]) == 1

    def test_delegated_to_attached(self):
        result = parse_plugin_output("[]", self.DELEGATED_TO)
        assert result["delegated_to"] == self.DELEGATED_TO


# ---------------------------------------------------------------------------
# error_result
# ---------------------------------------------------------------------------


class TestErrorResult:
    def test_structure(self):
        result = error_result("ruflo:jujutsu", "plugin missing")
        assert result["delegated_to"] == "ruflo:jujutsu"
        assert len(result["findings"]) == 1
        finding = result["findings"][0]
        assert finding["issue"] == "plugin missing"
        assert finding["severity"] == "high"
        assert set(finding.keys()) == FINDING_FIELDS

    def test_custom_severity_and_area(self):
        result = error_result("ruflo:browser", "timeout", severity="critical", area="qa")
        finding = result["findings"][0]
        assert finding["severity"] == "critical"
        assert finding["area"] == "qa"

    def test_fix_recommendation_present(self):
        result = error_result("ruflo:x", "msg")
        assert result["findings"][0]["fix_recommendation"] != ""


# ---------------------------------------------------------------------------
# invoke_ruflo
# ---------------------------------------------------------------------------


class TestInvokeRuflo:
    def test_builds_correct_argv(self):
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.stdout = "[]"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("fabric.molyanov.validators._common.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            invoke_ruflo("jujutsu", ["src/", "--flag"])
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd == ["ruflo", "jujutsu", "src/", "--flag"]

    def test_no_shell_true(self):
        with patch("fabric.molyanov.validators._common.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="", returncode=0
            )
            invoke_ruflo("jujutsu", [])
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("shell") is not True

    def test_file_not_found_propagates(self):
        with patch(
            "fabric.molyanov.validators._common.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(FileNotFoundError):
                invoke_ruflo("jujutsu", [])

    def test_timeout_propagates(self):
        with patch(
            "fabric.molyanov.validators._common.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ruflo"], timeout=1),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                invoke_ruflo("jujutsu", [], timeout=1)
