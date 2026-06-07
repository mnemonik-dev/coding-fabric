"""``restricted_env(kind)`` — allowlist for child-process environments.

Source of truth: tech-spec Data Models / "restricted_env(kind) allowlist table".

We build the dict by NAME from os.environ rather than mutating the parent env,
so a leaked secret in our process never reaches the child — even if a future
contributor accidentally adds it to /etc/blogger.env.
"""

from __future__ import annotations

import os
from typing import Literal

EnvKind = Literal["claude", "analyze_blog", "mnemonik-mcp", "blogger_inproc"]

_CLAUDE_ALLOWLIST = ("PATH", "HOME", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
_ANALYZE_BLOG_ALLOWLIST = ("PATH", "MNEMONIK_CLAUDE_BLOG_PATH")
_MNEMONIK_MCP_ALLOWLIST = ("PATH",)


def _pick(names: tuple[str, ...]) -> dict[str, str]:
    return {n: os.environ[n] for n in names if n in os.environ}


def restricted_env(kind: EnvKind) -> dict[str, str]:
    """Return an env dict suitable for ``subprocess.Popen(env=...)``.

    Per kind:
      * ``claude``         — PATH, HOME, plus EITHER CLAUDE_CODE_OAUTH_TOKEN or
                             ANTHROPIC_API_KEY. RuntimeError if both are absent
                             (silent auth failures are operationally awful).
      * ``analyze_blog``   — PATH, MNEMONIK_CLAUDE_BLOG_PATH.
      * ``mnemonik-mcp``   — PATH only; all payload is stdin MCP JSON-RPC.
      * ``blogger_inproc`` — full parent env (call is in-process).
    """
    if kind == "claude":
        env = _pick(_CLAUDE_ALLOWLIST)
        if "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env:
            raise RuntimeError(
                "restricted_env('claude'): neither CLAUDE_CODE_OAUTH_TOKEN nor "
                "ANTHROPIC_API_KEY is set; refusing to spawn claude without auth"
            )
        return env

    if kind == "analyze_blog":
        return _pick(_ANALYZE_BLOG_ALLOWLIST)

    if kind == "mnemonik-mcp":
        return _pick(_MNEMONIK_MCP_ALLOWLIST)

    if kind == "blogger_inproc":
        # In-process call: caller already has the full env. Returning a copy
        # keeps the type signature consistent with the subprocess branches.
        return dict(os.environ)

    raise ValueError(f"unknown restricted_env kind: {kind!r}")
