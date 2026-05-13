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
    sanitize_string,
)


# ---------------------------------------------------------------------------
# sanitize_string
# ---------------------------------------------------------------------------


class TestSanitizeString:
    def test_ansi_escape_stripped(self):
        assert sanitize_string("\x1b[31mred\x1b[0m") == "red"

    def test_ansi_bold_stripped(self):
        assert sanitize_string("\x1b[1mBold\x1b[0m text") == "Bold text"

    def test_ansi_multi_param_stripped(self):
        assert sanitize_string("\x1b[38;5;200mcolor\x1b[0m") == "color"

    def test_control_chars_stripped(self):
        # NUL, BEL, BS, FF, SO — all must be removed
        assert sanitize_string("\x00\x07\x08\x0c\x0e") == ""

    def test_tab_and_newline_preserved(self):
        # \t (0x09) and \n (0x0a) are legitimate whitespace
        assert sanitize_string("a\tb\nc") == "a\tb\nc"

    def test_del_stripped(self):
        assert sanitize_string("ab\x7fcd") == "abcd"

    def test_length_capped_at_4096(self):
        long_str = "x" * 5000
        result = sanitize_string(long_str)
        assert len(result) == 4096

    def test_ansi_then_length_cap(self):
        # ANSI sequences are stripped first, then the cap is applied
        # 4097 visible chars + ANSI codes → strip ANSI → cap at 4096
        content = "x" * 4097
        encoded = f"\x1b[31m{content}\x1b[0m"
        result = sanitize_string(encoded)
        assert len(result) == 4096

    def test_clean_string_unchanged(self):
        s = "Hello, world! 123"
        assert sanitize_string(s) == s

    def test_ansi_input_to_canonicalize_finding_is_sanitized(self):
        """ANSI in plugin finding fields must be stripped end-to-end."""
        raw = {
            "severity": "high",
            "message": "\x1b[31mSQL injection\x1b[0m",
            "fix": "\x1b[1mUse parameterised queries\x1b[0m",
            "path": "\x1b[32msrc/main.py\x1b[0m",
            "category": "\x1b[33msecurity\x1b[0m",
        }
        out = canonicalize_finding(raw)
        assert out["issue"] == "SQL injection"
        assert out["fix_recommendation"] == "Use parameterised queries"
        assert out["file"] == "src/main.py"
        assert out["area"] == "security"


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

    def test_unknown_defaults_to_error_not_info(self):
        """Unknown severity must default to 'error', not 'info', so criticals are not silently lost."""
        assert normalise_severity("totally-unknown-level") == "error"

    def test_unknown_severity_emits_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="fabric.molyanov.validators"):
            result = normalise_severity("custom-bogus-severity")
        assert result == "error"
        assert "custom-bogus-severity" in caplog.text

    def test_non_string_returns_error(self):
        assert normalise_severity(None) == "error"
        assert normalise_severity(42) == "error"
        assert normalise_severity([]) == "error"


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
        assert out["severity"] == "error"  # default is now "error"
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

    def test_line_invalid_string_defaults_to_none(self):
        """Non-numeric line value must silently fall back to None, not raise."""
        out = canonicalize_finding({"line": "not-a-number"})
        assert out["line"] is None

    def test_line_float_string_truncated_to_int(self):
        # int("3.5") raises ValueError; the guard should fall back to None
        out = canonicalize_finding({"line": "3.5"})
        assert out["line"] is None

    def test_severity_normalised(self):
        out = canonicalize_finding({"severity": "ERROR"})
        assert out["severity"] == "error"

    def test_alias_priority_canonical_wins(self):
        """When both 'issue' (canonical) and 'message' (alias) are present, 'issue' wins."""
        raw = {"message": "from alias", "issue": "from canonical"}
        out = canonicalize_finding(raw)
        assert out["issue"] == "from canonical"

    def test_alias_priority_alias_used_when_no_canonical(self):
        """When only the alias is present, it fills the canonical slot."""
        raw = {"message": "only alias"}
        out = canonicalize_finding(raw)
        assert out["issue"] == "only alias"

    def test_alias_priority_area_canonical_wins(self):
        raw = {"category": "from alias", "area": "from canonical"}
        out = canonicalize_finding(raw)
        assert out["area"] == "from canonical"

    def test_all_string_fields_sanitized(self):
        raw = {
            "issue": "\x1b[31mevil\x1b[0m",
            "fix_recommendation": "\x00hidden\x07chars",
            "file": "\x1b[32mfile.py\x1b[0m",
            "area": "\x1b[1mbold\x1b[0m",
            "severity": "high",
        }
        out = canonicalize_finding(raw)
        assert "\x1b" not in out["issue"]
        assert "\x1b" not in out["file"]
        assert "\x1b" not in out["area"]
        assert "\x00" not in out["fix_recommendation"]
        assert out["issue"] == "evil"


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
    def test_builds_correct_argv_with_separator(self):
        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.stdout = "[]"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("fabric.molyanov.validators._common.subprocess.run") as mock_run:
            mock_run.return_value = mock_result
            invoke_ruflo("jujutsu", ["src/"], ["extra-pos"])
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            # named_args before --, extra_args after --
            assert cmd == ["ruflo", "jujutsu", "src/", "--", "extra-pos"]

    def test_named_args_with_flags_before_separator(self):
        with patch("fabric.molyanov.validators._common.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            invoke_ruflo("security-audit", ["src/", "--profile", "owasp"])
            cmd = mock_run.call_args[0][0]
            assert cmd == ["ruflo", "security-audit", "src/", "--profile", "owasp", "--"]

    def test_extra_args_option_injection_rejected(self):
        with pytest.raises(ValueError, match="option-style argument"):
            with patch("fabric.molyanov.validators._common.subprocess.run"):
                invoke_ruflo("jujutsu", [], ["-r"])

    def test_extra_args_double_dash_injection_rejected(self):
        with pytest.raises(ValueError, match="option-style argument"):
            with patch("fabric.molyanov.validators._common.subprocess.run"):
                invoke_ruflo("jujutsu", [], ["--evil"])

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

    def test_timeout_capped_at_30_minutes(self):
        with patch("fabric.molyanov.validators._common.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            invoke_ruflo("jujutsu", [], timeout=99999)
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 1800

    def test_default_timeout_is_300(self):
        with patch("fabric.molyanov.validators._common.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
            invoke_ruflo("jujutsu", [])
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 300
