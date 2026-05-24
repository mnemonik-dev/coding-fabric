"""Decide which stage a ticket belongs to and run the engine for it.

The dispatch path:

  Ticket → stage (from labels) → workflow (load) → workspace (lease) →
  agent run → comment + state transition back to Kaneo.

Stage selection is driven by ticket labels:

  - `stage:code`   → load .symphony/workflows/code.md   (default)
  - `stage:review` → load .symphony/workflows/review.md
  - `stage:qa`     → load .symphony/workflows/qa.md

Falls back to ``code`` when no explicit stage label is present.

Workspace lifecycle: leverages the existing ``worktree`` module's
create / destroy primitives (the same machinery the legacy POST /worktree
endpoint used). Symphony just calls them directly instead of going
through HTTP.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from agent_runner import AgentRunResult, run_agent
from kaneo_client import KaneoClient, Ticket
from workflow_loader import WorkflowDefinition, load_workflow

logger = logging.getLogger(__name__)

_TASK_ID_RE = re.compile(r"^[A-Z0-9\-]{3,40}$")


@dataclass(frozen=True)
class DispatchOutcome:
    ticket_id: str
    stage: str
    engine: str
    workspace_path: Path | None
    result: AgentRunResult | None
    error: str | None = None


def stage_for(ticket: Ticket, default: str = "code") -> str:
    """Pick a stage name from ticket labels.

    Looks for any label of the form ``stage:<name>``. Returns ``default``
    when none present.
    """
    for label in ticket.labels:
        if label.startswith("stage:"):
            return label.removeprefix("stage:")
    return default


def task_id_for(ticket: Ticket) -> str:
    """Sanitize a ticket id into a Symphony task_id (matches ^[A-Z0-9-]{3,40}$)."""
    raw = (ticket.id or "").upper()
    cleaned = re.sub(r"[^A-Z0-9-]", "-", raw)
    cleaned = cleaned.strip("-") or "TASK"
    cleaned = cleaned[:40]
    if len(cleaned) < 3:
        cleaned = (cleaned + "---")[:3]
    if not _TASK_ID_RE.match(cleaned):
        raise ValueError(f"derived task_id failed validation: {cleaned!r}")
    return cleaned


async def dispatch_ticket(
    ticket: Ticket,
    *,
    kaneo: KaneoClient,
    repo_root: Path,
    workspace_root: Path,
) -> DispatchOutcome:
    """Run one ticket end-to-end. Caller is responsible for concurrency caps.

    Workflow selection comes from ``repo_root / .symphony / workflows /
    <stage>.md``. Workspace lease is per-task under ``workspace_root``.
    """
    stage = stage_for(ticket)
    task_id = task_id_for(ticket)

    try:
        workflow = load_workflow(repo_root, stage)
    except Exception as exc:
        logger.exception("dispatch: workflow load failed for ticket=%s stage=%s", ticket.id, stage)
        return DispatchOutcome(
            ticket_id=ticket.id,
            stage=stage,
            engine="?",
            workspace_path=None,
            result=None,
            error=f"workflow load: {exc}",
        )

    # Workspace lease — reuse the existing worktree machinery rather than
    # going through HTTP. Symphony is colocated, no need for the API hop.
    workspace_path = workspace_root / task_id
    workspace_path.mkdir(parents=True, exist_ok=True)
    # TODO(symphony): wire to worktree.create_worktree() once we have a
    # repo URL per topic (currently the workspace is just a fresh dir).

    rendered_prompt = _render_prompt(workflow, ticket)

    try:
        result = await run_agent(
            engine=workflow.engine,
            cwd=workspace_path,
            prompt=rendered_prompt,
            model=workflow.model,
            allowed_tools=workflow.allowed_tools,
        )
    except Exception as exc:
        logger.exception(
            "dispatch: agent run failed ticket=%s engine=%s", ticket.id, workflow.engine
        )
        await _safe_comment(
            kaneo,
            ticket.id,
            f"symphony: agent run failed ({workflow.engine}/{stage}): {exc}",
        )
        return DispatchOutcome(
            ticket_id=ticket.id,
            stage=stage,
            engine=workflow.engine,
            workspace_path=workspace_path,
            result=None,
            error=str(exc),
        )

    logger.info(
        "dispatch: ticket=%s stage=%s engine=%s exit=%s duration=%.1fs",
        ticket.id,
        stage,
        workflow.engine,
        result.exit_code,
        result.duration_seconds,
    )

    # Surface the run to Kaneo (truncated body — full transcript stays on
    # the VM filesystem under workspace_path).
    summary = _summarize_result(result)
    await _safe_comment(
        kaneo,
        ticket.id,
        f"symphony: {workflow.engine}/{stage} run complete (exit={result.exit_code}, "
        f"{result.duration_seconds:.1f}s)\n\n{summary}",
    )

    return DispatchOutcome(
        ticket_id=ticket.id,
        stage=stage,
        engine=workflow.engine,
        workspace_path=workspace_path,
        result=result,
    )


def _render_prompt(workflow: WorkflowDefinition, ticket: Ticket) -> str:
    """Combine workflow prompt + ticket context. Minimal templating."""
    return (
        f"{workflow.prompt}\n\n"
        f"--- ticket ---\n"
        f"id: {ticket.id}\n"
        f"title: {ticket.title}\n"
        f"status: {ticket.status}\n"
        f"labels: {', '.join(ticket.labels) or '-'}\n"
        f"\n{ticket.description}\n"
    )


def _summarize_result(result: AgentRunResult) -> str:
    """Trim stdout/stderr for Kaneo comment posting."""
    tail = result.stdout[-1500:] if result.stdout else ""
    if result.stderr:
        tail = (tail + "\n--- stderr ---\n" + result.stderr[-500:]).strip()
    return tail or "(no output)"


async def _safe_comment(kaneo: KaneoClient, ticket_id: str, body: str) -> None:
    try:
        await kaneo.create_comment(ticket_id, body)
    except Exception:
        logger.exception("dispatch: failed to post comment to ticket=%s", ticket_id)
