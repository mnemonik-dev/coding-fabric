"""Auto-merge with veto window.

When a ticket reaches the ``ready-to-merge`` status (set by qa.md after
acceptance), Symphony schedules ``gh pr merge`` for ``<now> +
veto_window_hours``. The deadline is persisted in two places so it
survives Symphony restarts AND operator visibility:

  1. The ticket itself gains a label ``auto-merge:YYYY-MM-DDTHH:MM:SSZ``
     — the wall-clock deadline. Kaneo is the source of truth.
  2. A Kaneo comment is posted on entering the veto window, addressed
     to ``policy.veto_topic`` (Telegram-side), explaining the operator
     command to abort.

The operator's veto path is independent of Symphony: typing
``/reject <TASK-ID>`` in the ``ops`` topic causes the bot to set the
ticket label ``rejected``. Symphony's tick sees that label and cancels
the auto-merge before the deadline.

Tick logic (called from ``poll_loop``):

  - For every ticket in status ``ready-to-merge``:
    - If a ``rejected`` label is present: clear the auto-merge label,
      post a "merge cancelled" comment, transition ticket to
      ``review``.
    - Else if the ``auto-merge:<iso>`` deadline has passed: run
      ``gh pr merge`` for the PR linked from the ticket; on success
      transition to ``done``.
    - Else: leave alone (still waiting).

This module is a pure helper — call sites (poll_loop, or a dedicated
``auto_merge_tick``) own the cadence. Schema-light so it works for any
Kaneo workspace.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from kaneo_client import KaneoClient, Ticket

logger = logging.getLogger(__name__)

_LABEL_AUTO_MERGE_PREFIX = "auto-merge:"
_LABEL_REJECTED = "rejected"
_LABEL_MERGED = "merged"
_PR_LINK_RE = re.compile(r"https?://github\.com/[^\s]+/pull/(\d+)")
# The deadline is persisted in the symphony scheduling comment. Reading
# it back on each tick = stateless across restarts. Format must match
# what schedule_or_tick writes.
_SCHEDULE_DEADLINE_RE = re.compile(
    r"symphony: ready to auto-merge\. Window closes at (\S+?)\."
)


@dataclass(frozen=True)
class MergePolicy:
    merge_policy: str = "auto-with-veto"
    veto_window_hours: int = 24
    veto_topic: str = "ops"
    veto_command: str = "/reject"


def policy_from_env_or_default() -> MergePolicy:
    """Read merge policy from env (Symphony service-level fallback)."""
    return MergePolicy(
        merge_policy=os.environ.get("SYMPHONY_MERGE_POLICY", "auto-with-veto"),
        veto_window_hours=int(os.environ.get("SYMPHONY_VETO_WINDOW_HOURS", "24")),
        veto_topic=os.environ.get("SYMPHONY_VETO_TOPIC", "ops"),
        veto_command=os.environ.get("SYMPHONY_VETO_COMMAND", "/reject"),
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_auto_merge_deadline(labels: Iterable[str]) -> datetime | None:
    """Legacy: read deadline from a label (kept for backward compat).

    Symphony 2026-05+ uses _read_deadline_from_comments instead — labels
    aren't durably writable from the REST client yet.
    """
    for label in labels:
        if label.startswith(_LABEL_AUTO_MERGE_PREFIX):
            iso = label.removeprefix(_LABEL_AUTO_MERGE_PREFIX)
            try:
                if iso.endswith("Z"):
                    iso = iso.removesuffix("Z") + "+00:00"
                return datetime.fromisoformat(iso)
            except ValueError:
                logger.warning("auto_merge: cannot parse deadline label %r", label)
                return None
    return None


async def _read_deadline_from_comments(kaneo: "KaneoClient", task_id: str) -> datetime | None:
    """Scan ticket comments for a previously-posted deadline. Stateless."""
    try:
        comments = await kaneo.list_comments(task_id)
    except Exception:
        logger.exception("auto_merge: list_comments(%s) failed", task_id)
        return None
    # Newest deadline wins (later schedule overrides earlier one if any).
    latest: datetime | None = None
    for c in comments:
        body = c.get("body") or c.get("content") or ""
        if not isinstance(body, str):
            continue
        m = _SCHEDULE_DEADLINE_RE.search(body)
        if not m:
            continue
        iso = m.group(1)
        if iso.endswith("Z"):
            iso = iso.removesuffix("Z") + "+00:00"
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def _extract_pr_number(ticket: Ticket) -> int | None:
    for source in (ticket.description, *(str(v) for v in ticket.raw.values() if isinstance(v, str))):
        m = _PR_LINK_RE.search(source)
        if m:
            return int(m.group(1))
    return None


async def schedule_or_tick(
    ticket: Ticket,
    *,
    kaneo: KaneoClient,
    policy: MergePolicy,
    repo_root: Path,
) -> str:
    """Return a short status string for logging.

    Idempotent: called every poll tick for every ``ready-to-merge``
    ticket. Decides whether to (a) start the veto window (first time),
    (b) cancel due to /reject, (c) execute gh pr merge, (d) wait.
    """
    if policy.merge_policy == "always-human-merge":
        return "skip: always-human-merge"

    deadline = _parse_auto_merge_deadline(ticket.labels) or await _read_deadline_from_comments(
        kaneo, ticket.id
    )

    # (b) operator vetoed
    if _LABEL_REJECTED in ticket.labels:
        await kaneo.create_comment(
            ticket.id,
            "symphony: auto-merge cancelled by operator veto. "
            "Returning ticket to `review`.",
        )
        await kaneo.update_task_status(ticket.id, "review")
        return "cancelled-veto"

    # (a) entering the window for the first time
    if deadline is None and policy.merge_policy != "full-auto":
        new_deadline = _now_utc() + timedelta(hours=policy.veto_window_hours)
        iso = new_deadline.strftime("%Y-%m-%dT%H:%M:%SZ")
        # We can't set labels directly via REST without per-Kaneo plumbing;
        # post the deadline as a comment + (best-effort) attach label via
        # the labels endpoint. The label IS the source of truth across
        # restarts, so the next tick can find it.
        await kaneo.create_comment(
            ticket.id,
            f"symphony: ready to auto-merge. Window closes at {iso}. "
            f"Reply `{policy.veto_command} {ticket.id}` in `{policy.veto_topic}` "
            f"to cancel.",
        )
        logger.info(
            "auto_merge: ticket=%s deadline=%s (label injection deferred to caller)",
            ticket.id,
            iso,
        )
        return f"scheduled:{iso}"

    # (d) deadline not reached
    if deadline is not None and _now_utc() < deadline:
        return f"waiting:{deadline.isoformat()}"

    # (c) deadline passed (or full-auto) → execute merge
    pr_number = _extract_pr_number(ticket)
    if pr_number is None:
        await kaneo.create_comment(
            ticket.id,
            "symphony: auto-merge skipped — no PR link found in ticket "
            "description. Add a github PR URL and Symphony will retry.",
        )
        return "skip: no PR link"

    proc = await asyncio.create_subprocess_exec(
        "gh",
        "pr",
        "merge",
        str(pr_number),
        "--squash",
        "--delete-branch",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        await kaneo.create_comment(
            ticket.id,
            f"symphony: `gh pr merge {pr_number}` failed "
            f"(exit {proc.returncode}). Stderr tail:\n\n"
            f"```\n{stderr[-1000:]}\n```",
        )
        return f"merge-failed:{proc.returncode}"

    await kaneo.create_comment(
        ticket.id,
        f"symphony: PR #{pr_number} merged. Closing ticket.",
    )
    await kaneo.update_task_status(ticket.id, "done")
    logger.info("auto_merge: merged PR #%s for ticket=%s", pr_number, ticket.id)
    return f"merged:{pr_number}"
