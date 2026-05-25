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

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent_runner import AgentRunResult, run_agent
from kaneo_client import KaneoClient, Ticket
from workflow_loader import WorkflowDefinition, load_workflow

logger = logging.getLogger(__name__)

_TASK_ID_RE = re.compile(r"^[A-Z0-9\-]{3,40}$")
# `repo: org/name` or `repo: https://github.com/org/name(.git)` in the
# Kaneo project description binds Symphony to a specific git repo for
# that project. Anything else (or absent description) → no clone, the
# workspace stays as a fresh `git init` (smoke-test mode).
_REPO_DIRECTIVE_RE = re.compile(
    r"^\s*repo:\s*(?P<repo>\S+)\s*$", re.MULTILINE | re.IGNORECASE
)


def parse_repo_from_description(description: str | None) -> str | None:
    """Pull a `repo: org/name` (or full URL) line out of a Kaneo project description."""
    if not description:
        return None
    m = _REPO_DIRECTIVE_RE.search(description)
    if not m:
        return None
    raw = m.group("repo").strip()
    if not raw:
        return None
    # Normalize org/name → https://github.com/org/name.git so we can
    # always git-clone the result. Full URLs pass through unchanged.
    if raw.startswith(("http://", "https://", "git@", "ssh://")):
        return raw
    if "/" in raw and len(raw.split("/")) == 2:
        return f"https://github.com/{raw}.git"
    return None  # invalid shape — operator should fix the description


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

    # Workspace lease + repo prep. The Kaneo project's description can
    # bind a GitHub repo via `repo: org/name` (or full URL); when present,
    # Symphony clones it into the workspace and resets to the default
    # branch so claude starts from a clean checkout. Without a binding,
    # the workspace is a fresh `git init` (smoke-test mode).
    workspace_path = workspace_root / task_id
    workspace_path.mkdir(parents=True, exist_ok=True)
    try:
        project = await kaneo.get_project(ticket.project_id)
    except Exception:
        logger.exception("dispatch: get_project(%s) failed", ticket.project_id)
        project = {}
    repo_url = parse_repo_from_description(
        project.get("description") if isinstance(project, dict) else None
    )

    try:
        await _prepare_workspace(workspace_path, repo_url)
    except Exception as exc:
        logger.exception("dispatch: workspace prep failed for ticket=%s", ticket.id)
        await _safe_comment(
            kaneo,
            ticket.id,
            f"symphony: workspace prep failed ({repo_url or 'no repo binding'}): {exc}",
        )
        return DispatchOutcome(
            ticket_id=ticket.id,
            stage=stage,
            engine=workflow.engine,
            workspace_path=workspace_path,
            result=None,
            error=str(exc),
        )

    rendered_prompt = _render_prompt(workflow, ticket)

    # GitHub auth for claude (so it can `gh pr create`/`git push`).
    # Sourced from the service env (set by the workspace-manager systemd
    # drop-in / Ansible role). When unset, claude can still commit locally
    # but won't be able to open a PR; that's a soft fail, not a hard one.
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    extra_env: dict[str, str] = {}
    if gh_token:
        extra_env["GITHUB_TOKEN"] = gh_token
        # `gh auth status` reads GH_TOKEN too; setting both avoids surprises.
        extra_env["GH_TOKEN"] = gh_token

    try:
        result = await run_agent(
            engine=workflow.engine,
            cwd=workspace_path,
            prompt=rendered_prompt,
            model=workflow.model,
            allowed_tools=workflow.allowed_tools,
            extra_env=extra_env or None,
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


async def _prepare_workspace(workspace_path: Path, repo_url: str | None) -> None:
    """Bring the workspace to a clean state for the agent run.

    Three cases:
    - No repo bound → ensure the dir exists and `git init` if needed
      (claude can still commit locally for smoke tests).
    - Repo bound, workspace empty → `git clone <repo_url> <workspace_path>`.
    - Repo bound, workspace already a clone → `git fetch --all` +
      `git reset --hard origin/<HEAD>` to drop any stale state from a
      previous dispatch.

    Auth: if GITHUB_TOKEN is in the service env, git clone/fetch over
    HTTPS picks it up automatically via the credential helper installed
    in the Ansible role. Without it, public repos still work; private
    ones fail loudly.
    """
    workspace_path.mkdir(parents=True, exist_ok=True)
    git_dir = workspace_path / ".git"

    if repo_url is None:
        if not git_dir.exists():
            await _run_git(workspace_path, ["init", "--quiet"])
        return

    # If the workspace already has a clone of a different repo, blow it
    # away — operator might have changed the binding between dispatches.
    if git_dir.exists():
        existing = await _run_git(
            workspace_path, ["config", "--get", "remote.origin.url"]
        )
        if existing.strip() != repo_url.strip():
            logger.info(
                "_prepare_workspace: remote changed (%s → %s); re-cloning",
                existing.strip(),
                repo_url,
            )
            import shutil

            shutil.rmtree(workspace_path)
            workspace_path.mkdir(parents=True, exist_ok=True)

    if not (workspace_path / ".git").exists():
        # Fresh clone. --depth 1 keeps it lean; full history is recoverable
        # via `git fetch --unshallow` from inside if claude needs it.
        await _run_git(
            workspace_path.parent,
            ["clone", "--depth", "1", repo_url, workspace_path.name],
        )
        return

    # Existing clone of the right repo — refresh.
    await _run_git(workspace_path, ["fetch", "--all", "--prune", "--quiet"])
    # Reset to the remote default branch. `git symbolic-ref` follows
    # origin/HEAD → origin/<default>.
    head = await _run_git(
        workspace_path, ["symbolic-ref", "refs/remotes/origin/HEAD"]
    )
    default_branch = head.strip().rsplit("/", 1)[-1] if head.strip() else "main"
    await _run_git(workspace_path, ["checkout", "-q", default_branch])
    await _run_git(
        workspace_path, ["reset", "--hard", f"origin/{default_branch}"]
    )
    await _run_git(workspace_path, ["clean", "-fdx"])


async def _run_git(cwd: Path, args: list[str]) -> str:
    """Run `git <args>` in cwd. Returns stdout. Raises on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', errors='replace')[-500:]}"
        )
    return stdout.decode("utf-8", errors="replace")


async def _safe_comment(kaneo: KaneoClient, ticket_id: str, body: str) -> None:
    try:
        await kaneo.create_comment(ticket_id, body)
    except Exception:
        logger.exception("dispatch: failed to post comment to ticket=%s", ticket_id)
