"""
fabric.molyanov.validators.security_auditor_wrapper
======================================================
Molyanov ``security-auditor`` validator delegating to the ruflo
``security-audit`` plugin.

CLI usage::

    python -m fabric.molyanov.validators.security_auditor_wrapper \\
        --target=path/to/file.py [--dry-run]

    echo '{"target": "src/", "profile": "owasp"}' | \\
        python -m fabric.molyanov.validators.security_auditor_wrapper

Output is a JSON object with ``findings`` list and ``delegated_to`` field.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from typing import Any

from fabric.logs.sanitizer import SanitizedStreamHandler
from fabric.molyanov.validators._common import (
    RUFLO_TIMEOUT_SECONDS,
    error_result,
    invoke_ruflo,
    parse_plugin_output,
)

PLUGIN = "security-audit"
DELEGATED_TO = f"ruflo:{PLUGIN}"

_logger = logging.getLogger("fabric.molyanov.validators.security_auditor")
if not _logger.handlers:
    _logger.addHandler(SanitizedStreamHandler(sys.stderr))
    _logger.setLevel(logging.WARNING)


def run(
    molyanov_input: dict[str, Any],
    *,
    dry_run: bool = False,
    timeout: int = RUFLO_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Delegate a security-auditor validation request to ruflo security-audit.

    Args:
        molyanov_input: Molyanov-format input dict.  Recognised keys:
            - ``target`` (str): path or glob passed to the plugin.
            - ``profile`` (str): audit profile forwarded as ``--profile``.
            - ``args`` (list[str]): extra CLI args forwarded verbatim.
        dry_run: When ``True``, log the intended invocation and return an empty
            findings list without executing the plugin.
        timeout: Subprocess timeout in seconds.

    Returns:
        Molyanov result dict with ``findings`` list and ``delegated_to`` field.
    """
    target: str = molyanov_input.get("target", "")
    profile: str = molyanov_input.get("profile", "")
    extra_args: list[str] = molyanov_input.get("args", [])

    plugin_args: list[str] = []
    if target:
        plugin_args.append(target)
    if profile:
        plugin_args.extend(["--profile", profile])
    plugin_args.extend(extra_args)

    if dry_run:
        cmd_preview = ["ruflo", PLUGIN, *plugin_args]
        _logger.info("dry-run: would invoke %s", cmd_preview)
        sys.stderr.write(f"delegating to {DELEGATED_TO}: {cmd_preview}\n")
        return {"findings": [], "delegated_to": DELEGATED_TO}

    _logger.info("delegating to %s", DELEGATED_TO)
    sys.stderr.write(f"delegating to {DELEGATED_TO}\n")

    try:
        result = invoke_ruflo(PLUGIN, plugin_args, timeout=timeout)
    except FileNotFoundError:
        msg = "ruflo binary not found on PATH; cannot invoke security-audit plugin"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="security-auditor")
    except subprocess.TimeoutExpired:
        msg = f"ruflo {PLUGIN} timed out after {timeout}s"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="security-auditor")

    if result.returncode not in (0, 1):
        stderr_snippet = result.stderr[:200] if result.stderr else ""
        _logger.error(
            "ruflo %s exited with code %d: %s",
            PLUGIN,
            result.returncode,
            stderr_snippet,
        )
        if not result.stdout.strip():
            msg = (
                f"ruflo {PLUGIN} exited with unexpected code {result.returncode}. "
                "Check that ruflo and the security-audit plugin are correctly installed."
            )
            return error_result(DELEGATED_TO, msg, area="security-auditor")

    return parse_plugin_output(result.stdout, DELEGATED_TO)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabric.molyanov.validators.security_auditor_wrapper",
        description=(
            "Delegate molyanov security-auditor checks to ruflo security-audit plugin."
        ),
    )
    parser.add_argument(
        "--target",
        default="",
        help="File or directory path to audit (forwarded to ruflo security-audit).",
    )
    parser.add_argument(
        "--profile",
        default="",
        help="Audit profile to use (e.g. 'owasp').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which plugin would be invoked without executing it.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    ns = parser.parse_args(argv)

    molyanov_input: dict[str, Any] = {}
    if not sys.stdin.isatty():
        try:
            molyanov_input = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"invalid JSON on stdin: {exc}\n")
            sys.exit(2)

    if ns.target:
        molyanov_input["target"] = ns.target
    if ns.profile:
        molyanov_input["profile"] = ns.profile

    output = run(molyanov_input, dry_run=ns.dry_run)
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
