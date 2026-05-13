"""
Tests for fabric.workspace_manager.logging_setup.

The single load-bearing invariant verified here: at every log level (including
DEBUG) the root logger MUST only have sanitized handler types attached.  A
plain ``logging.StreamHandler`` on the root logger would bypass T07 sanitization
for any record routed through it.

This test pins the contract addressed by audit finding T20-F-003 (sanitizer
bypass at DEBUG): the DEBUG-mode console handler must be a
``SanitizedStreamHandler``, never a plain ``logging.StreamHandler``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

# Make repo root importable so `fabric.logs.sanitizer` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# These imports require fabric.logs.sanitizer to be installed in the test env.
from fabric.logs.sanitizer.handler import (  # noqa: E402
    SanitizedFileHandler,
    SanitizedStreamHandler,
)

import logging_setup  # noqa: E402  workspace-manager pythonpath puts modules at top level


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Detach all handlers from the root logger before and after each test."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    for h in original_handlers:
        root.removeHandler(h)
    yield
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in original_handlers:
        root.addHandler(h)
    root.setLevel(original_level)


def _installed_handlers(root: logging.Logger) -> list[logging.Handler]:
    """Return only handlers that were installed by configure_logging().

    pytest attaches its own LogCaptureHandler to the root logger which we must
    ignore; the test is only interested in whatever the production startup
    sequence in configure_logging() chose to attach.
    """
    # LogCaptureHandler is internal to pytest; identify by module/class name.
    return [
        h for h in root.handlers
        if type(h).__module__.startswith("fabric.logs.sanitizer")
        or type(h).__name__ in {"SanitizedFileHandler", "SanitizedStreamHandler"}
        or type(h).__name__ in {"StreamHandler", "FileHandler", "RotatingFileHandler"}
    ]


@pytest.mark.parametrize("level_name", ["INFO", "WARNING", "ERROR", "DEBUG"])
def test_all_installed_handlers_are_sanitized(tmp_path: Path, level_name: str) -> None:
    """At every log level the handlers configure_logging() installs are sanitized."""
    log_file = tmp_path / "workspace-manager.log"
    logging_setup.configure_logging(log_file, level_name=level_name)
    root = logging.getLogger()
    installed = _installed_handlers(root)
    assert installed, "configure_logging() did not install any handler on the root logger"
    for h in installed:
        assert isinstance(h, (SanitizedFileHandler, SanitizedStreamHandler)), (
            f"At {level_name=} configure_logging() installed a non-sanitized handler: "
            f"{type(h).__name__}.  All workspace-manager handlers must scrub secrets."
        )


def test_debug_installs_sanitized_stream_handler(tmp_path: Path) -> None:
    """DEBUG level must add a SanitizedStreamHandler, never a plain StreamHandler.

    Regression test for audit finding T20 critical: previous DEBUG path attached
    a bare logging.StreamHandler() that bypassed sanitization on stderr.
    """
    log_file = tmp_path / "workspace-manager.log"
    logging_setup.configure_logging(log_file, level_name="DEBUG")
    root = logging.getLogger()

    sanitized_streams = [
        h for h in _installed_handlers(root)
        if isinstance(h, SanitizedStreamHandler)
    ]
    plain_streams = [
        h for h in _installed_handlers(root)
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and not isinstance(h, SanitizedStreamHandler)
    ]
    assert sanitized_streams, "DEBUG should install a SanitizedStreamHandler"
    assert not plain_streams, (
        f"DEBUG installed plain StreamHandler(s): {[type(h).__name__ for h in plain_streams]} — "
        "this would leak secrets to stderr / systemd journal."
    )
