"""
fabric.molyanov.validators.post_deploy_qa_wrapper
===================================================
Molyanov ``post-deploy-qa`` validator delegating to the ruflo ``browser``
plugin.

CLI usage::

    python -m fabric.molyanov.validators.post_deploy_qa_wrapper \\
        --url=https://example.com [--dry-run]

    echo '{"url": "https://example.com", "scenario": "smoke"}' | \\
        python -m fabric.molyanov.validators.post_deploy_qa_wrapper

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

PLUGIN = "browser"
DELEGATED_TO = f"ruflo:{PLUGIN}"

_logger = logging.getLogger("fabric.molyanov.validators.post_deploy_qa")
if not _logger.handlers:
    _logger.addHandler(SanitizedStreamHandler(sys.stderr))
    _logger.setLevel(logging.WARNING)


def run(
    molyanov_input: dict[str, Any],
    *,
    dry_run: bool = False,
    timeout: int = RUFLO_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Delegate a post-deploy-qa validation request to ruflo browser.

    Args:
        molyanov_input: Molyanov-format input dict.  Recognised keys:
            - ``url`` (str): target URL passed to the plugin.
            - ``scenario`` (str): test scenario forwarded as ``--scenario``.
            - ``args`` (list[str]): extra CLI args forwarded verbatim.
        dry_run: When ``True``, log the intended invocation and return an empty
            findings list without executing the plugin.
        timeout: Subprocess timeout in seconds.

    Returns:
        Molyanov result dict with ``findings`` list and ``delegated_to`` field.
    """
    url: str = molyanov_input.get("url", "")
    scenario: str = molyanov_input.get("scenario", "")
    extra_args: list[str] = molyanov_input.get("args", [])

    plugin_args: list[str] = []
    if url:
        plugin_args.append(url)
    if scenario:
        plugin_args.extend(["--scenario", scenario])
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
        msg = "ruflo binary not found on PATH; cannot invoke browser plugin"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="post-deploy-qa")
    except subprocess.TimeoutExpired:
        msg = f"ruflo {PLUGIN} timed out after {timeout}s"
        _logger.error(msg)
        return error_result(DELEGATED_TO, msg, area="post-deploy-qa")

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
                "Check that ruflo and the browser plugin are correctly installed."
            )
            return error_result(DELEGATED_TO, msg, area="post-deploy-qa")

    return parse_plugin_output(result.stdout, DELEGATED_TO)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabric.molyanov.validators.post_deploy_qa_wrapper",
        description=(
            "Delegate molyanov post-deploy-qa checks to ruflo browser plugin."
        ),
    )
    parser.add_argument(
        "--url",
        default="",
        help="Target URL for the browser test (forwarded to ruflo browser).",
    )
    parser.add_argument(
        "--scenario",
        default="",
        help="Test scenario name (e.g. 'smoke').",
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

    if ns.url:
        molyanov_input["url"] = ns.url
    if ns.scenario:
        molyanov_input["scenario"] = ns.scenario

    output = run(molyanov_input, dry_run=ns.dry_run)
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
