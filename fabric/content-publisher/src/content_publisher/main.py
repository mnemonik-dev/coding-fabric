"""Entry point + FastAPI lifespan for content-publisher.

Boot order (Decision 7 + Decision 8):
  1. PUBLISH_MODE=auto ⇒ two-location token match enforced; mismatch → SystemExit.
  2. ``recover_in_flight(queue.jsonl)`` synchronously.
  3. Start three asyncio tasks (each logs its own start line — Task 7 smoke
     greps for these regexes):
        queue-poll started
        deadline-checker started
        cleanup-gc started

FastAPI is imported lazily inside the factory so importing this module from
tests (which mostly need ``assert_auto_mode_token_match_or_exit``) does not
require FastAPI to be installed.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def assert_auto_mode_token_match_or_exit() -> None:
    """Enforce Decision 7: PUBLISH_MODE=auto requires a 2-location token match.

    The two locations:
      * ``PUBLISH_AUTO_MODE_TOKEN``         — comes from ``/etc/blogger.env``;
      * ``PUBLISH_AUTO_MODE_TOKEN_CONFIRM`` — comes from sops, rendered by
                                              Ansible into the same env file
                                              under a separate key.

    Both must be non-empty and equal. Constant-time compare via ``hmac.compare_digest``
    so a side-channel cannot probe the secret. On any failure: ``SystemExit(2)``
    with a loud message — the worker MUST NOT continue.
    """
    mode = os.environ.get("PUBLISH_MODE", "approval").lower()
    if mode != "auto":
        return

    a = os.environ.get("PUBLISH_AUTO_MODE_TOKEN", "")
    b = os.environ.get("PUBLISH_AUTO_MODE_TOKEN_CONFIRM", "")
    if not a or not b:
        logger.error(
            "PUBLISH_MODE=auto but PUBLISH_AUTO_MODE_TOKEN/_CONFIRM is empty; "
            "refusing to start (Decision 7 two-location binding)"
        )
        raise SystemExit(2)
    if not hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8")):
        logger.error(
            "PUBLISH_MODE=auto but PUBLISH_AUTO_MODE_TOKEN does not match "
            "PUBLISH_AUTO_MODE_TOKEN_CONFIRM; refusing to start "
            "(Decision 7 two-location binding)"
        )
        raise SystemExit(2)


def _queue_path() -> Path:
    return Path(
        os.environ.get(
            "CONTENT_PUBLISHER_QUEUE", "/var/lib/content-publisher/queue.jsonl"
        )
    )


async def queue_poll_loop() -> None:
    """Long-running queue-poll sub-loop (writing→preview-sent + publishing).

    Walks queue.jsonl every N seconds: ``queued`` jobs are run through
    ``worker.run_writing_to_preview``; ``publishing`` jobs through
    ``publish.publish_step``. Cadence ~5s, default-overridable via env.
    """
    from content_publisher import publish, queue, worker

    logger.info("queue-poll started")
    interval = float(os.environ.get("PUBLISH_QUEUE_POLL_INTERVAL_SECONDS", "5"))
    try:
        while True:
            try:
                for job in queue.load(_queue_path()):
                    if job.status.value == "queued":
                        await worker.run_writing_to_preview(job)
                    elif job.status.value == "publishing":
                        await publish.publish_step(job_id=job.id)
            except Exception:  # noqa: BLE001
                logger.exception("queue-poll tick failed")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("queue-poll cancelled")
        raise


@asynccontextmanager
async def lifespan(app: object = None) -> AsyncIterator[None]:
    """FastAPI-compatible lifespan. Runs the boot order from the module docstring."""
    from content_publisher import cleanup, deadline, recovery

    assert_auto_mode_token_match_or_exit()

    qp = _queue_path()
    recovery.recover_in_flight(qp)

    tasks = [
        asyncio.create_task(queue_poll_loop(), name="queue-poll"),
        asyncio.create_task(
            deadline.deadline_checker_loop(qp), name="deadline-checker"
        ),
        asyncio.create_task(cleanup.cleanup_gc_loop(qp), name="cleanup-gc"),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        # Drain cancellations so we don't leak warnings on shutdown.
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def create_app() -> Any:
    """Build the FastAPI app object. Importing FastAPI is deferred so unit
    tests do not require it.
    """
    from fastapi import FastAPI  # type: ignore[import-not-found]

    return FastAPI(
        title="content-publisher",
        version="0.1.0",
        lifespan=lifespan,
    )


def main() -> int:
    """Entry point invoked by ``python -m content_publisher`` or the systemd unit."""
    logging.basicConfig(level=logging.INFO)
    try:
        import uvicorn  # type: ignore[import-not-found]

        bind = os.environ.get("CONTENT_PUBLISHER_BIND_IP", "127.0.0.1")
        port = int(os.environ.get("CONTENT_PUBLISHER_BIND_PORT", "8788"))
        uvicorn.run(create_app(), host=bind, port=port)
    except ImportError:
        # No HTTP layer requested — run the lifespan directly as a daemon.
        async def _run() -> None:
            async with lifespan():
                stop = asyncio.Event()
                await stop.wait()

        asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
