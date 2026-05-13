"""
Bootstrap the root logger.

In production the root handler MUST be fabric.logs.sanitizer.handler.SanitizedFileHandler
(Task 07).  Failing to load that handler is a hard startup failure so that secrets are
never written to log files without sanitisation.

In tests the WORKSPACE_MANAGER_REQUIRE_SANITIZER environment variable can be set to
"false" to allow the plain RotatingFileHandler fallback.  The default is "true".
"""

from __future__ import annotations

import importlib
import logging
import logging.handlers
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRE_SANITIZER = os.environ.get("WORKSPACE_MANAGER_REQUIRE_SANITIZER", "true").lower() != "false"


def _load_sanitized_handler(log_file: Path, level: int) -> logging.Handler:
    """
    Load SanitizedFileHandler.  Raises ImportError if the module is absent and
    WORKSPACE_MANAGER_REQUIRE_SANITIZER is true (the production default).
    """
    try:
        module = importlib.import_module("fabric.logs.sanitizer.handler")
        cls = getattr(module, "SanitizedFileHandler")
        handler: logging.Handler = cls(str(log_file))
        handler.setLevel(level)
        return handler
    except (ImportError, AttributeError) as exc:
        if _REQUIRE_SANITIZER:
            print(
                "FATAL: fabric.logs.sanitizer.handler.SanitizedFileHandler could not be "
                "imported.  workspace-manager refuses to start without the sanitizer to "
                "prevent secrets from being written to log files in plaintext.  "
                f"Original error: {exc}\n"
                "Fix: ensure the fabric-logs-sanitizer package (Task 07) is installed in "
                "the same virtualenv, or set WORKSPACE_MANAGER_REQUIRE_SANITIZER=false "
                "only in development environments.",
                file=sys.stderr,
            )
            raise ImportError(
                "fabric.logs.sanitizer.handler not available; refusing to start"
            ) from exc
        # Development / test fallback — never active in production.
        fallback: logging.Handler = logging.handlers.RotatingFileHandler(
            str(log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        fallback.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        fallback.setFormatter(formatter)
        logging.getLogger().warning(
            "fabric.logs.sanitizer.handler not available; "
            "using unsanitized RotatingFileHandler (dev/test mode only)"
        )
        return fallback


def _load_sanitized_stream_handler(level: int) -> logging.Handler:
    """
    Load SanitizedStreamHandler for the DEBUG-mode console handler.

    A plain ``logging.StreamHandler`` would bypass sanitization for stderr, allowing
    secrets to reach the systemd journal in plaintext at DEBUG level (Finding F-003
    in T20 code-audit).  We refuse to start without the sanitized variant in
    production; the same WORKSPACE_MANAGER_REQUIRE_SANITIZER env knob controls dev
    fallback behaviour.
    """
    try:
        module = importlib.import_module("fabric.logs.sanitizer.handler")
        cls = getattr(module, "SanitizedStreamHandler")
        handler: logging.Handler = cls()
        handler.setLevel(level)
        return handler
    except (ImportError, AttributeError) as exc:
        if _REQUIRE_SANITIZER:
            print(
                "FATAL: fabric.logs.sanitizer.handler.SanitizedStreamHandler could not "
                "be imported.  workspace-manager refuses to install a non-sanitized "
                "DEBUG console handler.  "
                f"Original error: {exc}",
                file=sys.stderr,
            )
            raise ImportError(
                "fabric.logs.sanitizer.handler.SanitizedStreamHandler not available; "
                "refusing to install plaintext StreamHandler at DEBUG"
            ) from exc
        # Development fallback only (never active in production).
        fallback: logging.Handler = logging.StreamHandler()
        fallback.setLevel(level)
        logging.getLogger().warning(
            "fabric.logs.sanitizer.handler.SanitizedStreamHandler not available; "
            "using unsanitized StreamHandler (dev/test mode only)"
        )
        return fallback


def configure_logging(log_file: Path, level_name: str = "INFO") -> None:
    level = logging.getLevelName(level_name.upper())
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = _load_sanitized_handler(log_file, level)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on re-import in tests
    if not any(type(h).__name__ in ("SanitizedFileHandler", "RotatingFileHandler") for h in root.handlers):
        root.addHandler(handler)

    # Console handler only in DEBUG to avoid leaking to systemd journal.
    # MUST use SanitizedStreamHandler so secrets are scrubbed before reaching stderr.
    if level == logging.DEBUG:
        if not any(type(h).__name__ in ("SanitizedStreamHandler", "StreamHandler") for h in root.handlers):
            console = _load_sanitized_stream_handler(logging.DEBUG)
            root.addHandler(console)
