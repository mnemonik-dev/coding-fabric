"""Run ``analyze_blog.py`` and parse its score / issues JSON output.

Issues are truncated to the first three INSIDE this module — operator preview
fits in a Telegram message and ranks issues by importance (upstream contract).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from content_publisher.env import restricted_env

logger = logging.getLogger(__name__)

# Upstream contract: analyze_blog.py lives at this path on the VM (Task 3 role
# clones claude-blog to /opt/claude-blog at a pinned SHA).
ANALYZE_BLOG_PATH = "/opt/claude-blog/scripts/analyze_blog.py"

_STDERR_TAIL_BYTES = 2000
_ISSUES_PREVIEW_CAP = 3


@dataclass(frozen=True)
class ScoreResult:
    score: int
    issues: list[str]


class ScoreFailed(Exception):
    def __init__(self, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


def _parse_stdout(stdout: bytes) -> ScoreResult:
    """Parse analyze_blog stdout (JSON: ``{"score": int, "issues": [str, ...]}``).

    Truncates issues to the first 3 — operator preview cap.
    """
    text = stdout.decode("utf-8", errors="replace").strip()
    payload = json.loads(text)
    score = int(payload["score"])
    raw_issues = payload.get("issues") or []
    if not isinstance(raw_issues, list):
        raise ValueError(
            f"analyze_blog issues must be a list, got {type(raw_issues).__name__}"
        )
    issues = [str(i) for i in raw_issues[:_ISSUES_PREVIEW_CAP]]
    return ScoreResult(score=score, issues=issues)


async def analyze(article_path: Path) -> ScoreResult:
    """Run ``python /opt/claude-blog/scripts/analyze_blog.py <article>`` and parse stdout."""
    env = restricted_env("analyze_blog")
    proc = await asyncio.create_subprocess_exec(
        "python",
        ANALYZE_BLOG_PATH,
        str(article_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stderr_tail = stderr_bytes[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise ScoreFailed(
            f"analyze_blog exited with code {proc.returncode}",
            stderr_tail=stderr_tail,
        )
    try:
        return _parse_stdout(stdout_bytes)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ScoreFailed(
            f"analyze_blog stdout could not be parsed: {exc}",
            stderr_tail=stderr_tail,
        ) from exc
