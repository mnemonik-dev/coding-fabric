"""Spawn the ``claude`` CLI as a subprocess with the blog-writer skill.

Decision 1: standalone subprocess, prompt via stdin (NOT argv), env strictly
allowlisted by ``restricted_env('claude')`` so the operator-bot's
``TELEGRAM_BOT_TOKEN`` and sops-loaded secrets cannot leak into a subprocess.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from content_publisher.env import restricted_env

logger = logging.getLogger(__name__)

# stderr is potentially large (skill traces); only the tail is relevant for
# diagnostics and we keep it small so it fits in the Job.error_message JSON
# field without bloating the queue line.
_STDERR_TAIL_BYTES = 2000

_CLAUDE_ARGV = (
    "claude",
    "--print",
    "--mcp-config",
    "/etc/blogger.mcp.json",
    "--strict-mcp-config",
    "--skill",
    "claude-blog/blog-writer",
)


class SpawnFailed(Exception):
    """``claude`` exited non-zero or did not produce ``article.md``.

    ``stderr_tail`` is a short text snippet from the subprocess's stderr,
    safe to persist in ``Job.error_message``.
    """

    def __init__(self, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


async def spawn_claude(*, prompt: str, worktree: Path) -> Path:
    """Run ``claude`` in ``worktree`` with ``prompt`` on stdin; return path to article.md.

    Contract:
      * argv is the fixed tuple above — no operator-controlled token reaches argv;
      * env is exactly ``restricted_env('claude')`` (PATH, HOME, OAuth/API key);
      * stdin gets ``prompt`` once and is then closed (via ``communicate``);
      * on success, ``<worktree>/article.md`` exists and is returned;
      * on non-zero exit or missing article, raises ``SpawnFailed``.
    """
    env = restricted_env("claude")
    proc = await asyncio.create_subprocess_exec(
        *_CLAUDE_ARGV,
        cwd=str(worktree),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate(input=prompt.encode("utf-8"))
    stderr_tail = stderr_bytes[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise SpawnFailed(
            f"claude exited with code {proc.returncode}",
            stderr_tail=stderr_tail,
        )

    article_path = worktree / "article.md"
    if not article_path.exists():
        raise SpawnFailed(
            "claude exited 0 but article.md was not produced",
            stderr_tail=stderr_tail,
        )
    return article_path
