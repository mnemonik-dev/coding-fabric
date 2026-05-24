"""Unit tests for workflow_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflow_loader import WorkflowError, load_workflow, workflow_path


def _write_workflow(repo: Path, stage: str, content: str) -> Path:
    p = workflow_path(repo, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_parses_basic_workflow(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "code",
        """---
engine: claude
model: claude-sonnet-4-6
allowed_tools:
  - Skill
  - Bash
  - mcp__kaneo
poll_filters:
  states:
    any_of: [todo]
---
Write code per Molyanov methodology.
""",
    )
    wf = load_workflow(tmp_path, "code")
    assert wf.stage == "code"
    assert wf.engine == "claude"
    assert wf.model == "claude-sonnet-4-6"
    assert wf.allowed_tools == ("Skill", "Bash", "mcp__kaneo")
    assert wf.prompt.startswith("Write code per Molyanov")
    assert wf.poll_filters == {"states": {"any_of": ["todo"]}}


def test_missing_engine_raises(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "code",
        """---
allowed_tools: [Skill]
---
body
""",
    )
    with pytest.raises(WorkflowError, match="missing required `engine`"):
        load_workflow(tmp_path, "code")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkflowError, match="workflow not found"):
        load_workflow(tmp_path, "nonexistent")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "code",
        """---
engine: claude
  - not yaml
   ::: bad
---
body
""",
    )
    with pytest.raises(WorkflowError, match="invalid YAML"):
        load_workflow(tmp_path, "code")


def test_string_tools_csv(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "review",
        """---
engine: codex
allowed_tools: Skill, Bash, mcp__kaneo
---
review prompt
""",
    )
    wf = load_workflow(tmp_path, "review")
    assert wf.allowed_tools == ("Skill", "Bash", "mcp__kaneo")


def test_null_model_normalized(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "qa",
        """---
engine: claude
model: null
---
qa
""",
    )
    wf = load_workflow(tmp_path, "qa")
    assert wf.model is None


def test_mtime_cache_picks_up_changes(tmp_path: Path) -> None:
    import os
    import time

    p = _write_workflow(
        tmp_path,
        "code",
        """---
engine: claude
---
first
""",
    )
    wf1 = load_workflow(tmp_path, "code")
    assert wf1.prompt == "first"

    # Re-write with bumped mtime
    time.sleep(0.01)
    p.write_text(
        """---
engine: codex
---
second
""",
        encoding="utf-8",
    )
    # Force mtime forward enough for the cache key to change
    new_mtime = p.stat().st_mtime + 1
    os.utime(p, (new_mtime, new_mtime))
    wf2 = load_workflow(tmp_path, "code")
    assert wf2.engine == "codex"
    assert wf2.prompt == "second"
