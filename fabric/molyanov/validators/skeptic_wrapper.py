"""
fabric.molyanov.validators.skeptic_wrapper
============================================
Molyanov ``skeptic`` validator delegating to the ruflo ``jujutsu`` plugin.

CLI usage::

    python -m fabric.molyanov.validators.skeptic_wrapper \\
        --target=path/to/file.py [--dry-run]

    echo '{"target": "src/"}' | \\
        python -m fabric.molyanov.validators.skeptic_wrapper

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

PLUGIN = "jujutsu"
DELEGATED_TO = f"ruflo:{PLUGIN}"

_logger = logging.getLogger("fabric.molyanov.validators.skeptic")
if not _logger.handlers:
    _logger.addHandler(SanitizedStreamHandler(sys.stderr))
    _logger.setLevel(logging.WARNING)


def run(
    molyanov_input: dict[str, Any],
    *,
    dry_run: bool = False,
    timeout: int = RUFLO_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Delegate a skeptic validation request to ruflo jujutsu.

    Args:
        molyanov_input: Molyanov-format input dict.  Recognised keys:
            - ``target`` (str): path or glob passed to the plugin.
            - ``args`` (list[str]): extra CLI args forwarded verbatim.
        dry_run: When ``True``, log the intended invocation and return an empty
            findings list without executing the plugin.
        timeout: Subprocess timeout in seconds.

    Returns:
        Molyanov result dict with ``findings`` list and ``delegated_to`` field.
    """
    target: str = molyanov_input.get("target", "")
    extra_args: list[str] = molyanov_input.get("args", [])

    named_args: list[str] = []
    if target:
        named_args.append(target)

    if dry_run:
        cmd_preview = ["ruflo", PLUGIN, *named_args, "--", *extra_args]
        _logger.info("dry-run: would invoke %s", cmd_preview)
        return {"findings": [], "delegated_to": DELEGATED_TO}

    _logger.info("delegating to %s", DELEGATED_TO)

    try:
        result = invoke_ruflo(PLUGIN, named_args, extra_args, timeout=timeout)
    except ValueError as exc:
        msg = f"invalid extra_args: {exc}"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="skeptic")
    except FileNotFoundError:
        msg = "ruflo binary not found on PATH; cannot invoke jujutsu plugin"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="skeptic")
    except subprocess.TimeoutExpired:
        msg = f"ruflo {PLUGIN} timed out after {timeout}s"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="skeptic")

    if result.returncode not in (0, 1):
        # 0 = success/no findings, 1 = findings present (conventional)
        # any other code = plugin execution error
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
                "Check that ruflo and the jujutsu plugin are correctly installed."
            )
            return error_result(DELEGATED_TO, msg, area="skeptic")

    return parse_plugin_output(result.stdout, DELEGATED_TO)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabric.molyanov.validators.skeptic_wrapper",
        description="Delegate molyanov skeptic checks to ruflo jujutsu plugin.",
    )
    parser.add_argument(
        "--target",
        default="",
        help="File or directory path to inspect (forwarded to ruflo jujutsu).",
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

    # Merge stdin JSON (if any) with CLI flags; CLI flags take precedence.
    molyanov_input: dict[str, Any] = {}
    if not sys.stdin.isatty():
        try:
            molyanov_input = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            _logger.error("invalid JSON on stdin: %s", exc)
            sys.exit(2)

    if ns.target:
        molyanov_input["target"] = ns.target

    output = run(molyanov_input, dry_run=ns.dry_run)
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
