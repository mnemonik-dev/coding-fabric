"""Thin MCP-stdio client for ``mnemonic_sign_memory``.

Wire contract (Decision 3, Architecture step 4):
  * argv is exactly ``[<binary>, "mcp-stdio"]`` — no payload, no flags.
  * env is ``restricted_env('mnemonik-mcp')`` (PATH only).
  * stdin carries MCP JSON-RPC, newline-delimited:
      1) ``initialize``
      2) ``tools/call`` for ``mnemonic_sign_memory``
  * stdin is closed after the call → subprocess exits cleanly.
  * receipt = ``attestation_hash`` field inside the tool response's JSON text.

Framing format — newline-delimited JSON. The MCP spec lists two transports
(LSP-style Content-Length headers or NDJSON); Task 2's npm-pinned binary uses
NDJSON, verified offline during install-time handshake. If a future upstream
ships Content-Length framing, swap ``_speak`` and add a regression test —
this is the only choke point.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from content_publisher.env import restricted_env

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "content-publisher", "version": "0.1"}
_TOOL_NAME = "mnemonic_sign_memory"
_STDERR_TAIL_BYTES = 2000
# Hard cap: we never expect either the initialize handshake or the sign call
# to take longer than this. Without a timeout, a hung mnemonik-mcp would
# deadlock the worker indefinitely.
_PER_CALL_TIMEOUT_SECONDS = 30.0


class MCPSignFailed(Exception):
    """Raised on any failure of the attestation envelope.

    ``stderr_tail`` carries up to 2000 bytes of subprocess stderr for the
    operator to diagnose (e.g. binary missing, daemon not running).
    """

    def __init__(self, message: str, *, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


def _binary() -> str:
    """Resolve the mnemonik-mcp binary path.

    Ansible (Task 2) discovers the real binary name at deploy time and writes
    it into the systemd unit's environment as ``MNEMONIK_MCP_BINARY``. The fact
    cascade is ``which mnemonik-mcp || which mnemonic-mcp`` (Decision 3).
    """
    bin_ = os.environ.get("MNEMONIK_MCP_BINARY")
    if not bin_:
        raise MCPSignFailed(
            "MNEMONIK_MCP_BINARY is not set; Ansible role mnemonic-mcp must "
            "discover the binary and template it into the unit environment"
        )
    return bin_


async def _send_frame(proc: asyncio.subprocess.Process, frame: dict[str, Any]) -> None:
    assert proc.stdin is not None
    line = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
    proc.stdin.write(line)
    await proc.stdin.drain()


async def _recv_frame(proc: asyncio.subprocess.Process) -> dict[str, Any]:
    assert proc.stdout is not None
    line = await asyncio.wait_for(
        proc.stdout.readline(), timeout=_PER_CALL_TIMEOUT_SECONDS
    )
    if not line:
        raise MCPSignFailed("mnemonik-mcp closed stdout before responding")
    try:
        parsed: dict[str, Any] = json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPSignFailed(f"mnemonik-mcp emitted non-JSON: {exc}") from exc
    return parsed


def _extract_attestation_hash(tool_response: dict[str, Any]) -> str:
    """Return the ``attestation_hash`` from an MCP ``tools/call`` response.

    MCP tool responses wrap the payload in ``result.content[0].text`` — a JSON
    string. We parse it and look for ``attestation_hash``.
    """
    if "error" in tool_response:
        err = tool_response["error"]
        raise MCPSignFailed(
            f"mnemonic_sign_memory failed: "
            f"{err.get('message', err)} (code={err.get('code')})"
        )
    result = tool_response.get("result") or {}
    content = result.get("content") or []
    if not content:
        raise MCPSignFailed(
            f"mnemonic_sign_memory returned empty content: {tool_response!r}"
        )
    text = content[0].get("text", "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MCPSignFailed(
            f"mnemonic_sign_memory content[0].text is not JSON: {exc}"
        ) from exc
    h = payload.get("attestation_hash")
    if not isinstance(h, str) or not h:
        raise MCPSignFailed(
            f"mnemonic_sign_memory response missing 'attestation_hash': {payload!r}"
        )
    return h


async def sign_memory(
    *,
    content: str,
    content_sha256: str,
    post_url: str,
    score: int,
    prompt: str,
) -> str:
    """Speak MCP JSON-RPC to ``mnemonik-mcp mcp-stdio`` and return the receipt.

    Raises:
        MCPSignFailed: on any subprocess / protocol / signer error.
    """
    binary = _binary()
    env = restricted_env("mnemonik-mcp")

    proc = await asyncio.create_subprocess_exec(
        binary,
        "mcp-stdio",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_tail = ""
    try:
        # 1) initialize.
        await _send_frame(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            },
        )
        init_resp = await _recv_frame(proc)
        if "error" in init_resp:
            raise MCPSignFailed(
                f"mnemonik-mcp initialize failed: {init_resp['error']!r}"
            )

        # 2) tools/call mnemonic_sign_memory.
        await _send_frame(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": _TOOL_NAME,
                    "arguments": {
                        "content": content,
                        "content_sha256": content_sha256,
                        "post_url": post_url,
                        "score": score,
                        "prompt": prompt,
                    },
                },
            },
        )
        tool_resp = await _recv_frame(proc)

        # 3) close stdin → subprocess exits.
        if proc.stdin is not None:
            proc.stdin.close()

        attestation_hash = _extract_attestation_hash(tool_resp)
        return attestation_hash
    except TimeoutError as exc:
        raise MCPSignFailed("mnemonik-mcp timed out") from exc
    except BaseException:
        # SIGTERM cancels us — ensure mnemonik-mcp does not survive as orphan.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except BaseException:
                pass
        raise
    finally:
        # Drain stderr for diagnostics. Best-effort.
        if proc.stderr is not None:
            try:
                stderr_bytes = await asyncio.wait_for(
                    proc.stderr.read(), timeout=1.0
                )
                stderr_tail = stderr_bytes[-_STDERR_TAIL_BYTES:].decode(
                    "utf-8", errors="replace"
                )
            except (TimeoutError, Exception):  # noqa: BLE001
                pass
        # Wait for exit so we never leak a zombie. Bounded.
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except BaseException:
                pass
        if proc.returncode not in (0, None) and stderr_tail:
            logger.warning(
                "mnemonik-mcp exited with code %s; stderr tail: %s",
                proc.returncode,
                stderr_tail,
            )
