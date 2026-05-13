"""
fabric.watchdog
===============
Operational watchdog for the coding-fabric loop VM.

Runs 9 alert checks every 5 minutes (via systemd timer).  Each check module
exposes a single ``check(config) -> Alert | None`` function.  Alerts are
batched (>3 in one tick -> digest), posted to the ops Telegram topic, and
optionally converted to Kaneo cards via /turn-into-task.

All log output is routed through fabric.logs.sanitizer.handler.SanitizedFileHandler.
The import is a hard dependency — watchdog refuses to start without it.
"""

from fabric.watchdog.models import Alert, Severity

__all__ = ["Alert", "Severity"]
