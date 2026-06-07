"""Concurrent callback + deadline-fire → exactly one publish.

This is the race ``cas_status`` is designed to win. The deadline-checker and
the bot-callback both try to CAS ``preview-sent → publishing``; exactly one
succeeds, the loser sees ``StaleStateError``.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from content_publisher import queue
from content_publisher.models import JobStatus


def test_concurrent_callback_and_deadline_fire_publishes_once(
    tmp_queue_path: Path, make_job
) -> None:
    """Two threads race to CAS preview-sent → publishing. Exactly one wins."""
    job = make_job(
        status=JobStatus.PREVIEW_SENT,
        approval_deadline=datetime.now(UTC) - timedelta(seconds=1),
        preview_pending=False,
    )
    tmp_queue_path.write_text(job.model_dump_json() + "\n", encoding="utf-8")

    barrier = threading.Barrier(2)
    outcomes: list = []

    def attempt(label: str) -> None:
        barrier.wait()
        try:
            queue.cas_status(
                tmp_queue_path,
                job.id,
                expected=JobStatus.PREVIEW_SENT,
                target=JobStatus.PUBLISHING,
            )
            outcomes.append((label, "win"))
        except queue.StaleStateError as exc:
            outcomes.append((label, "lose", exc.actual_status))

    t1 = threading.Thread(target=attempt, args=("callback",))
    t2 = threading.Thread(target=attempt, args=("deadline",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    wins = [o for o in outcomes if o[1] == "win"]
    losses = [o for o in outcomes if o[1] == "lose"]
    assert len(wins) == 1, outcomes
    assert len(losses) == 1, outcomes
    assert losses[0][2] == JobStatus.PUBLISHING

    refreshed = queue.load(tmp_queue_path)[0]
    assert refreshed.status == JobStatus.PUBLISHING
