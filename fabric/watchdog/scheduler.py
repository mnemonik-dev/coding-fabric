"""
fabric.watchdog.scheduler
==========================
Runs all 9 alert checks concurrently with a per-check 30-second timeout.
Aggregates results, deduplicates against the 24h alert-state cache, then
dispatches to Telegram (and optionally Kaneo for /turn-into-task).

Batching: if >3 new alerts fire in one tick, they are combined into a single
digest message rather than spamming the ops topic individually.

Hard requirement: fabric.logs.sanitizer.handler.SanitizedFileHandler must be
importable.  The scheduler refuses to initialize otherwise.
"""

from __future__ import annotations

import concurrent.futures
import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fabric.watchdog.models import Alert
from fabric.watchdog.alert_state import AlertStateStore
from fabric.watchdog.telegram import TelegramPoster
from fabric.watchdog.kaneo import KaneoClient

# Hard-fail on missing sanitizer (T07 requirement).
_REQUIRE_SANITIZER = os.environ.get("WATCHDOG_REQUIRE_SANITIZER", "true").lower() != "false"
try:
    from fabric.logs.sanitizer.handler import SanitizedFileHandler  # noqa: F401
except ImportError as exc:
    if _REQUIRE_SANITIZER:
        print(
            "FATAL: fabric.logs.sanitizer.handler.SanitizedFileHandler could not be "
            "imported.  watchdog refuses to start without the sanitizer. "
            f"Original error: {exc}\n"
            "Set WATCHDOG_REQUIRE_SANITIZER=false only in dev/test environments.",
            file=sys.stderr,
        )
        raise ImportError(
            "fabric.logs.sanitizer.handler not available; refusing to start"
        ) from exc

logger = logging.getLogger(__name__)

_CHECK_MODULES = [
    "fabric.watchdog.checks.orphaned_worktrees",
    "fabric.watchdog.checks.hung_tmux",
    "fabric.watchdog.checks.solana_rpc",
    "fabric.watchdog.checks.irys_balance",
    "fabric.watchdog.checks.stale_swarms",
    "fabric.watchdog.checks.disk_pressure",
    "fabric.watchdog.checks.master_drift",
    "fabric.watchdog.checks.stale_prs",
    "fabric.watchdog.checks.failed_attestation",
    "fabric.watchdog.checks.stale_lkg",
]

_PER_CHECK_TIMEOUT = 30  # seconds
_BATCH_THRESHOLD = 3     # >3 alerts -> digest


# Checks that belong to the critical-class (AC11-AC16 attestation chain).
# Disabling any of these defeats the corresponding audit controls.
_CRITICAL_CLASS_CHECKS: frozenset[str] = frozenset([
    "failed_attestation",
])


def _run_check(module_name: str, config: dict[str, Any]) -> Alert | None:
    """Import check module and call check(config), returning Alert or None.

    Config keys:
        disabled_checks (list[str]): check names to skip.
        acknowledge_disable_consequences (bool): must be explicitly True when
            disabling a critical-class check; otherwise the disable is ignored
            and a warning is logged.  This documents operator intent and
            prevents silent security regressions (e.g. disabling
            ``failed_attestation`` defeats AC11-AC16).
    """
    disabled_checks: list[str] = config.get("disabled_checks", [])
    check_name = module_name.rsplit(".", 1)[-1]
    if check_name in disabled_checks:
        if check_name in _CRITICAL_CLASS_CHECKS:
            ack = config.get("acknowledge_disable_consequences", False)
            if not ack:
                logger.error(
                    "scheduler: refusing to disable critical-class check %s — "
                    "disabling failed_attestation defeats AC11-AC16. "
                    "Set acknowledge_disable_consequences=true in config to override.",
                    check_name,
                )
                # Fall through and run the check rather than silently skip it.
            else:
                logger.warning(
                    "scheduler: critical-class check %s disabled with acknowledged consequences",
                    check_name,
                )
                return None
        else:
            logger.debug("scheduler: check %s is disabled via config", check_name)
            return None
    try:
        mod = importlib.import_module(module_name)
        return mod.check(config)
    except Exception as exc:
        logger.error("scheduler: check module %s raised: %s", module_name, exc)
        return None


class Scheduler:
    """Run all checks in parallel and dispatch alerts."""

    def __init__(
        self,
        config: dict[str, Any],
        state_file: Path | None = None,
        telegram: TelegramPoster | None = None,
        kaneo: KaneoClient | None = None,
    ) -> None:
        self._config = config
        state_path = state_file or Path(
            config.get("alert_state_file", "/home/op/.fabric/watchdog/state/alerts.json")
        )
        self._state = AlertStateStore(state_path)
        self._telegram = telegram or TelegramPoster()
        self._kaneo = kaneo or KaneoClient()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(self) -> list[Alert]:
        """Run all checks, notify on new alerts, return the list of new alerts."""
        all_alerts = self._run_all_checks()
        new_alerts = [a for a in all_alerts if not self._state.is_seen(a.alert_id)]

        if not new_alerts:
            logger.info("scheduler: tick complete — no new alerts")
            return []

        # Persist before sending so a crash during send doesn't re-trigger.
        for alert in new_alerts:
            self._state.mark_seen(alert.alert_id, alert_class=alert.alert_class)

        self._dispatch(new_alerts)
        return new_alerts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_all_checks(self) -> list[Alert]:
        """Run all check modules concurrently; enforce per-check 30s timeout.

        Each check runs in its own thread.  After all tasks are submitted we
        drain them one-by-one with ``future.result(timeout=_PER_CHECK_TIMEOUT)``.
        A check that hangs beyond its timeout is logged and skipped; remaining
        checks continue collecting results.  The ThreadPoolExecutor context
        manager is exited with ``cancel_futures=True`` so pending (not yet
        started) futures are cancelled, but we do **not** wait for threads that
        have already started — Python threads cannot be forcibly killed.  This
        means a truly stuck check will continue running in the background, but
        the scheduler tick returns promptly.
        """
        results: list[Alert] = []
        # Use shutdown(wait=False) pattern: submit all, then collect with per-future timeout.
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=len(_CHECK_MODULES), thread_name_prefix="watchdog-check"
        )
        futures = {
            executor.submit(_run_check, mod, self._config): mod
            for mod in _CHECK_MODULES
        }
        # Shut down the pool without waiting so the loop below controls timing.
        # cancel_futures=True cancels pending (not-yet-started) futures immediately.
        executor.shutdown(wait=False, cancel_futures=True)

        for future, mod in futures.items():
            try:
                alert = future.result(timeout=_PER_CHECK_TIMEOUT)
                if alert is not None:
                    results.append(alert)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "scheduler: check %s timed out after %ss; skipping",
                    mod,
                    _PER_CHECK_TIMEOUT,
                )
            except Exception as exc:
                logger.error("scheduler: check %s raised unexpected error: %s", mod, exc)

        return results

    def _dispatch(self, alerts: list[Alert]) -> None:
        """Send alerts individually or as a digest (>3 in one tick)."""
        if len(alerts) > _BATCH_THRESHOLD:
            logger.info("scheduler: %d alerts — sending as digest", len(alerts))
            self._telegram.post_digest(alerts)
        else:
            for alert in alerts:
                logger.info(
                    "scheduler: dispatching alert alert_class=%s alert_id=%s",
                    alert.alert_class,
                    alert.alert_id,
                )
                self._telegram.post_alert(alert)
