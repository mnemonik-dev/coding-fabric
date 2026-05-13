"""Tests for fabric.molyanov.validators.skeptic_wrapper (ruflo jujutsu)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from fabric.molyanov.validators.skeptic_wrapper import DELEGATED_TO, PLUGIN, run


JUJUTSU_FINDINGS_FIXTURE = [
    {
        "severity": "high",
        "category": "logic",
        "path": "src/bad.py",
        "lineno": "10",
        "message": "Unreachable branch",
        "fix": "Remove dead code",
    },
    {
        "severity": "low",
        "category": "style",
        "path": "src/bad.py",
        "lineno": "22",
        "message": "Inconsistent naming",
        "fix": "Follow project conventions",
    },
]


def _make_completed(stdout: str, returncode: int = 0) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.stderr = ""
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# test_skeptic_invokes_jujutsu
# ---------------------------------------------------------------------------


class TestSkepticInvokesJujutsu:
    """Verify that the skeptic wrapper calls ruflo with the jujutsu plugin."""

    def test_invokes_jujutsu_plugin(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"target": "src/"})
            mock_invoke.assert_called_once()
            call_args, call_kwargs = mock_invoke.call_args
            assert call_args[0] == PLUGIN  # "jujutsu"

    def test_target_forwarded_as_first_arg(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"target": "src/main.py"})
            _, kwargs = mock_invoke.call_args
            positional = mock_invoke.call_args[0]
            plugin_args = positional[1]
            assert "src/main.py" in plugin_args

    def test_delegated_to_is_ruflo_jujutsu(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            result = run({})
            assert result["delegated_to"] == "ruflo:jujutsu"

    def test_extra_args_forwarded(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("[]")
            run({"target": "src/", "args": ["extra-pos"]})
            # extra_args are passed as the third positional to invoke_ruflo
            extra = mock_invoke.call_args[0][2]
            assert "extra-pos" in extra


# ---------------------------------------------------------------------------
# test_normalises_to_molyanov_schema (golden output)
# ---------------------------------------------------------------------------


class TestNormalisesToMolyanovSchema:
    def test_golden_output_shape(self):
        import json

        stdout = json.dumps(JUJUTSU_FINDINGS_FIXTURE)
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout)
            result = run({"target": "src/bad.py"})

        assert result["delegated_to"] == "ruflo:jujutsu"
        assert len(result["findings"]) == 2

        first = result["findings"][0]
        assert first["severity"] == "high"
        assert first["area"] == "logic"
        assert first["file"] == "src/bad.py"
        assert first["line"] == 10
        assert first["issue"] == "Unreachable branch"
        assert first["fix_recommendation"] == "Remove dead code"

        second = result["findings"][1]
        assert second["severity"] == "low"
        assert second["line"] == 22

    def test_finding_has_all_required_fields(self):
        import json

        stdout = json.dumps([JUJUTSU_FINDINGS_FIXTURE[0]])
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
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
    def test_no_ruflo_binary_returns_actionable_error(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo",
            side_effect=FileNotFoundError,
        ):
            result = run({"target": "src/"})

        assert result["delegated_to"] == "ruflo:jujutsu"
        assert len(result["findings"]) == 1
        finding = result["findings"][0]
        assert finding["severity"] in ("high", "critical")
        assert "ruflo" in finding["issue"].lower()
        assert "ruflo" in finding["fix_recommendation"].lower()

    def test_error_finding_has_all_fields(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo",
            side_effect=FileNotFoundError,
        ):
            result = run({})

        from fabric.molyanov.validators._common import FINDING_FIELDS

        assert set(result["findings"][0].keys()) == FINDING_FIELDS


# ---------------------------------------------------------------------------
# test_propagates_findings
# ---------------------------------------------------------------------------


class TestPropagatesFindings:
    """Known-bad fixture → findings list in molyanov format."""

    def test_propagates_all_findings(self):
        import json

        # Mix of severity levels and field aliases
        fixture = [
            {
                "severity": "critical",
                "category": "security",
                "path": "src/auth.py",
                "lineno": 5,
                "message": "Hardcoded credential",
                "fix": "Use environment variable",
            },
            {
                "severity": "warning",
                "message": "Unused import",
            },
        ]
        stdout = json.dumps(fixture)
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout, returncode=1)
            result = run({"target": "src/auth.py"})

        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "critical"
        assert result["findings"][0]["file"] == "src/auth.py"
        assert result["findings"][1]["severity"] == "low"  # warning → low

    def test_non_zero_exit_with_valid_output_returns_findings(self):
        import json

        stdout = json.dumps([{"severity": "high", "message": "Bad thing"}])
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed(stdout, returncode=1)
            result = run({})

        assert len(result["findings"]) == 1

    def test_unexpected_exit_code_with_empty_output_returns_error(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            mock_invoke.return_value = _make_completed("", returncode=127)
            result = run({})

        assert len(result["findings"]) == 1
        assert "unexpected code" in result["findings"][0]["issue"].lower()


# ---------------------------------------------------------------------------
# test_timeout_returns_actionable_error
# ---------------------------------------------------------------------------


class TestTimeoutReturnsActionableError:
    def test_timeout_returns_actionable_error(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo",
            side_effect=subprocess.TimeoutExpired(cmd=["ruflo"], timeout=300),
        ):
            result = run({"target": "src/"})

        assert result["delegated_to"] == "ruflo:jujutsu"
        assert len(result["findings"]) == 1
        finding = result["findings"][0]
        assert "timed out" in finding["issue"].lower()
        assert finding["severity"] in ("high", "critical")


# ---------------------------------------------------------------------------
# test_extra_args_option_injection
# ---------------------------------------------------------------------------


class TestExtraArgsOptionInjection:
    def test_option_arg_in_extra_args_returns_error(self):
        """An option-style argument in args must be rejected as an error result."""
        result = run({"target": "src/", "args": ["-r"]})
        assert result["delegated_to"] == "ruflo:jujutsu"
        assert len(result["findings"]) == 1
        assert "invalid extra_args" in result["findings"][0]["issue"].lower()

    def test_long_option_in_extra_args_returns_error(self):
        result = run({"args": ["--evil-flag"]})
        assert "invalid extra_args" in result["findings"][0]["issue"].lower()


# ---------------------------------------------------------------------------
# test_dry_run_flag
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    def test_dry_run_does_not_invoke_ruflo(self):
        with patch(
            "fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"
        ) as mock_invoke:
            result = run({"target": "src/"}, dry_run=True)
            mock_invoke.assert_not_called()

    def test_dry_run_returns_empty_findings(self):
        with patch("fabric.molyanov.validators.skeptic_wrapper.invoke_ruflo"):
            result = run({}, dry_run=True)
        assert result["findings"] == []

    def test_dry_run_preserves_delegated_to(self):
        result = run({}, dry_run=True)
        assert result["delegated_to"] == "ruflo:jujutsu"
