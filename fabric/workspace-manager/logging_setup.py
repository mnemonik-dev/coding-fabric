"""
Bootstrap the root logger.

Attempts to use fabric.logs.sanitizer.handler.SanitizedFileHandler (Task 07).
If the sanitizer module is not yet installed (parallel task), falls back to a
plain RotatingFileHandler so the service stays runnable in development.

All log emissions — including bw CLI output captured as structured fields — are
routed through this handler.  vault.py MUST NOT log raw secret values; only
structured keys such as {"topic": "docs", "item_count": 3} are logged.
"""

from __future__ import annotations

import importlib
import logging
import logging.handlers
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _try_sanitized_handler(log_file: Path, level: int) -> logging.Handler | None:
    try:
        module = importlib.import_module("fabric.logs.sanitizer.handler")
        cls = getattr(module, "SanitizedFileHandler")
        handler: logging.Handler = cls(str(log_file))
        handler.setLevel(level)
        return handler
    except (ImportError, AttributeError):
        return None


def configure_logging(log_file: Path, level_name: str = "INFO") -> None:
    level = logging.getLevelName(level_name.upper())
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = _try_sanitized_handler(log_file, level)
    if handler is None:
        fallback = logging.handlers.RotatingFileHandler(
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
        handler = fallback
        logging.getLogger().info(
            "fabric.logs.sanitizer.handler not available; using RotatingFileHandler"
        )

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on re-import in tests
    if not any(type(h).__name__ in ("SanitizedFileHandler", "RotatingFileHandler") for h in root.handlers):
        root.addHandler(handler)

    # Console handler only in DEBUG to avoid leaking to systemd journal
    if level == logging.DEBUG:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG)
        root.addHandler(console)
