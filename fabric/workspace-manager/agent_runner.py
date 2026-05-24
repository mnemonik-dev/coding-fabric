"""Spawn a coding-agent subprocess inside a leased workspace.

Wraps claude / codex CLI invocation. Engine-specific argv assembly is
isolated behind a tiny adapter table so adding qwen / hermes later is
adding a new entry to ``_ADAPTERS`` rather than touching the dispatch
logic.

The runner is intentionally minimal — it does not own ticket-state
writes, retry policy, or workflow selection. Those live in
``dispatch.py`` / ``poll_loop.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class EngineAdapter(Protocol):
    """Engine-specific argv assembly for non-interactive single-turn runs."""

    name: str

    def is_available(self) -> bool: ...

    def build_argv(
        self,
        *,
        prompt: str,
        mcp_config: str | None,
        model: str | None,
        allowed_tools: tuple[str, ...],
    ) -> list[str]: ...


class _ClaudeAdapter:
    name = "claude"

    def is_available(self) -> bool:
        from shutil import which

        return which("claude") is not None

    def build_argv(
        self,
        *,
        prompt: str,
        mcp_config: str | None,
        model: str | None,
        allowed_tools: tuple[str, ...],
    ) -> list[str]:
        argv = [
            "claude",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
            "--max-turns",
            "100",
        ]
        if model:
            argv += ["--model", model]
        if allowed_tools:
            argv += ["--allowedTools", ",".join(allowed_tools)]
        if mcp_config:
            argv += ["--mcp-config", mcp_config, "--strict-mcp-config"]
        argv += ["-p", prompt]
        return argv


class _CodexAdapter:
    name = "codex"

    def is_available(self) -> bool:
        from shutil import which

        return which("codex") is not None

    def build_argv(
        self,
        *,
        prompt: str,
        mcp_config: str | None,
        model: str | None,
        allowed_tools: tuple[str, ...],
    ) -> list[str]:
        argv = ["codex", "exec", "--full-auto"]
        if model:
            argv += ["--model", model]
        # Codex does not accept Claude-style --mcp-config; per-server
        # configs are passed via --config server=... entries. Skipped for
        # the skeleton — codex_mcp.build_codex_mcp_config_args in the bot
        # is the existing reference impl when we wire this for real.
        argv += [prompt]
        return argv


_ADAPTERS: dict[str, EngineAdapter] = {
    "claude": _ClaudeAdapter(),
    "codex": _CodexAdapter(),
}


def get_adapter(engine: str) -> EngineAdapter:
    adapter = _ADAPTERS.get(engine)
    if adapter is None:
        raise ValueError(
            f"unknown engine: {engine!r} (known: {sorted(_ADAPTERS)}). "
            f"Add an adapter in agent_runner._ADAPTERS to extend."
        )
    return adapter


async def run_agent(
    *,
    engine: str,
    cwd: Path,
    prompt: str,
    mcp_config: str | None = None,
    model: str | None = None,
    allowed_tools: tuple[str, ...] = (),
    extra_env: dict[str, str] | None = None,
    timeout_seconds: float = 1800.0,
) -> AgentRunResult:
    """Spawn the engine in ``cwd`` with ``prompt`` and collect the run."""
    adapter = get_adapter(engine)
    if not adapter.is_available():
        raise RuntimeError(f"engine {engine!r} not installed on PATH")

    argv = adapter.build_argv(
        prompt=prompt,
        mcp_config=mcp_config,
        model=model,
        allowed_tools=allowed_tools,
    )
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    logger.info(
        "agent_runner: spawning engine=%s cwd=%s argv_head=%s",
        engine,
        cwd,
        argv[:5],
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    duration = loop.time() - started
    return AgentRunResult(
        exit_code=proc.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        duration_seconds=duration,
    )
