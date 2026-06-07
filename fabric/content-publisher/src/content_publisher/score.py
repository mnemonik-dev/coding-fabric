"""Run ``analyze_blog.py`` and parse its score / issues JSON output.

Issues are truncated to the first three INSIDE this module — operator preview
fits in a Telegram message and ranks issues by importance (upstream contract).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from content_publisher.env import restricted_env

logger = logging.getLogger(__name__)

# Default install root for claude-blog (Task 3 Ansible role). The actual root
# is resolved at call time from ``MNEMONIK_CLAUDE_BLOG_PATH`` so that operator
# sops changes take effect without a code change — restricted_env already
# passes the env var to the subprocess, so this keeps them in sync.
_DEFAULT_CLAUDE_BLOG_ROOT = "/opt/claude-blog"
_ANALYZE_BLOG_REL = "scripts/analyze_blog.py"

_STDERR_TAIL_BYTES = 2000
_ISSUES_PREVIEW_CAP = 3


def _analyze_blog_path() -> Path:
    root = os.environ.get("MNEMONIK_CLAUDE_BLOG_PATH", _DEFAULT_CLAUDE_BLOG_ROOT)
    return Path(root) / _ANALYZE_BLOG_REL


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
    """Run ``python <MNEMONIK_CLAUDE_BLOG_PATH>/scripts/analyze_blog.py <article>``.

    Path root is read from ``MNEMONIK_CLAUDE_BLOG_PATH`` at call time, falling
    back to ``/opt/claude-blog`` if unset — matching the env var that
    ``restricted_env('analyze_blog')`` already passes through to the subprocess.
    """
    env = restricted_env("analyze_blog")
    proc = await asyncio.create_subprocess_exec(
        "python",
        str(_analyze_blog_path()),
        str(article_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await proc.communicate()
    except BaseException:
        # systemd SIGTERM cancels our coroutine — ensure analyze_blog does not
        # survive as an orphan with open pipes.
        if proc.returncode is None:
            proc.terminate()
            try:
                await proc.wait()
            except BaseException:
                pass
        raise
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
