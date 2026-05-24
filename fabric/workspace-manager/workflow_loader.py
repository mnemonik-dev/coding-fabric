"""Read per-stage workflow definitions from a repo's .symphony/workflows/.

Each repo Symphony manages drops one or more workflow markdown files at
``.symphony/workflows/<stage>.md`` (typically code.md, review.md, qa.md).
A workflow is plain markdown with YAML front matter — same shape as the
openai/symphony SPEC.md ``Workflow Definition`` entity (§4.1.2):

    ---
    engine: claude          # claude | codex | qwen | hermes | …
    model: null             # optional engine-specific model id
    allowed_tools:          # optional override; Symphony has sensible defaults
      - Skill
      - Bash
      - mcp__kaneo
    poll_filters:           # optional ticket selector for this stage
      labels:
        any_of: [code]
      states:
        any_of: [todo, in_progress]
    cleanup_on:             # optional cleanup policy
      states: [done, cancelled]
    ---
    You are working on TASK-ABC. Follow the Molyanov methodology …
    (markdown prompt body — passed to the engine as the system prompt)

Symphony loads the file per poll tick; mtime-cached so a workflow edit
lands without restart.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# yaml is in stdlib via PyYAML — already pulled in by pydantic-settings transitively
import yaml


@dataclass(frozen=True)
class WorkflowDefinition:
    """Parsed .symphony/workflows/<stage>.md."""

    stage: str
    engine: str
    model: str | None
    allowed_tools: tuple[str, ...]
    prompt: str
    poll_filters: dict[str, Any] = field(default_factory=dict)
    cleanup_on: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


class WorkflowError(Exception):
    pass


_cache: dict[Path, tuple[float, WorkflowDefinition]] = {}
_cache_lock = threading.Lock()


def workflow_path(repo_root: Path, stage: str) -> Path:
    return repo_root / ".symphony" / "workflows" / f"{stage}.md"


def load_workflow(repo_root: Path, stage: str) -> WorkflowDefinition:
    """Read + parse .symphony/workflows/<stage>.md with mtime cache."""
    path = workflow_path(repo_root, stage)
    if not path.exists():
        raise WorkflowError(f"workflow not found: {path}")
    mtime = path.stat().st_mtime
    with _cache_lock:
        cached = _cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
    parsed = _parse_workflow_file(path, stage)
    with _cache_lock:
        _cache[path] = (mtime, parsed)
    return parsed


def _parse_workflow_file(path: Path, stage: str) -> WorkflowDefinition:
    raw = path.read_text(encoding="utf-8")
    # Split front matter (delimited by --- lines) from body
    front_matter: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
                if isinstance(fm, dict):
                    front_matter = fm
                body = parts[2].lstrip("\n")
            except yaml.YAMLError as exc:
                raise WorkflowError(f"{path}: invalid YAML front matter: {exc}") from exc

    engine = str(front_matter.get("engine") or "").strip()
    if not engine:
        raise WorkflowError(f"{path}: missing required `engine` field in front matter")

    model_raw = front_matter.get("model")
    model = str(model_raw).strip() if model_raw else None
    if model in {"", "null", "None"}:
        model = None

    raw_tools = front_matter.get("allowed_tools") or []
    if isinstance(raw_tools, str):
        allowed_tools = tuple(t.strip() for t in raw_tools.split(",") if t.strip())
    elif isinstance(raw_tools, list):
        allowed_tools = tuple(str(t).strip() for t in raw_tools if str(t).strip())
    else:
        allowed_tools = ()

    poll_filters = front_matter.get("poll_filters") or {}
    if not isinstance(poll_filters, dict):
        poll_filters = {}
    cleanup_on = front_matter.get("cleanup_on") or {}
    if not isinstance(cleanup_on, dict):
        cleanup_on = {}

    return WorkflowDefinition(
        stage=stage,
        engine=engine,
        model=model,
        allowed_tools=allowed_tools,
        prompt=body.strip(),
        poll_filters=poll_filters,
        cleanup_on=cleanup_on,
        config=front_matter,
    )
