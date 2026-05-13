"""
Entry point for ``python -m fabric.watchdog``.

Loads config from environment, runs a single scheduler tick, and exits.
Called by the systemd timer every 5 minutes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Logging must be set up before importing scheduler (which enforces sanitizer).
_LOG_FILE = os.environ.get(
    "WATCHDOG_LOG_FILE", "/home/op/.fabric/logs/sanitized/watchdog.log"
)
_LOG_LEVEL = os.environ.get("WATCHDOG_LOG_LEVEL", "INFO")

_REQUIRE_SANITIZER = os.environ.get("WATCHDOG_REQUIRE_SANITIZER", "true").lower() != "false"

try:
    from fabric.logs.sanitizer.handler import SanitizedFileHandler

    _log_path = Path(_LOG_FILE)
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _handler = SanitizedFileHandler(str(_log_path))
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.getLogger().addHandler(_handler)
    logging.getLogger().setLevel(_LOG_LEVEL)
except ImportError as exc:
    if _REQUIRE_SANITIZER:
        print(
            f"FATAL: Cannot import SanitizedFileHandler: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    logging.basicConfig(level=_LOG_LEVEL)

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    cfg_file = os.environ.get("WATCHDOG_CONFIG_FILE", "")
    if cfg_file and Path(cfg_file).is_file():
        try:
            with open(cfg_file) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load config file %s: %s; using env defaults", cfg_file, exc)
    # Fall back to environment-driven defaults.
    return {}


def main() -> None:
    config = _load_config()
    from fabric.watchdog.scheduler import Scheduler

    scheduler = Scheduler(config)
    new_alerts = scheduler.tick()
    logger.info("watchdog tick complete: %d new alert(s) dispatched", len(new_alerts))


if __name__ == "__main__":
    main()
