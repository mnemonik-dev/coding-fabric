"""Centralized pytest fixtures for the content-publisher package.

These fixtures are intentionally shared across Tasks 4, 5, 6, and 8 — the worker
sub-loops (writing/scoring/publish/attest) and the telegram bot all import them.
Adding new fixtures here is a deliberate cross-task contract change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from content_publisher.models import Job, JobStatus


@pytest.fixture()
def tmp_queue_dir(tmp_path: Path) -> Path:
    """Isolated directory used to host the queue.jsonl + its .lock sibling."""
    d = tmp_path / "queue-data"
    d.mkdir()
    return d


@pytest.fixture()
def tmp_queue_path(tmp_queue_dir: Path) -> Path:
    """Path to queue.jsonl inside tmp_queue_dir.

    The file is NOT pre-created — `append_job` and `cas_status` must tolerate
    a missing-but-creatable file (first-ever write).
    """
    return tmp_queue_dir / "queue.jsonl"


@pytest.fixture()
def make_job():
    """Factory that returns a Job with a fresh UUID v4 and sensible defaults.

    All required Pydantic fields are filled so callers only need to specify the
    fields under test.
    """

    def _factory(**overrides: Any) -> Job:
        defaults: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(UTC),
            "prompt": "test prompt",
            "mode": "approval",
            "status": JobStatus.QUEUED,
        }
        defaults.update(overrides)
        return Job(**defaults)

    return _factory


@pytest.fixture()
def mock_analyze_blog() -> MagicMock:
    """Placeholder for the analyze_blog.py scorer; refined by Task 5."""
    m = MagicMock()
    m.return_value = {"score": 90, "issues": []}
    return m


@pytest.fixture()
def mock_blogger_run_campaign_from_article() -> MagicMock:
    """Placeholder for mnemonik_blogger.agent.run_campaign_from_article; refined by Task 6."""
    return MagicMock()


@pytest.fixture()
def mock_mnemonic_mcp_stdio() -> MagicMock:
    """Placeholder for the mnemonik-mcp stdio subprocess seam; refined by Task 6."""
    return MagicMock()


@pytest.fixture()
def mock_claude_subprocess() -> MagicMock:
    """Placeholder for asyncio.create_subprocess_exec('claude', ...); refined by Task 5."""
    m = MagicMock()
    m.returncode = 0
    return m
