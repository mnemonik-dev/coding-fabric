"""AC-T8: MCP-stdio attestation envelope hygiene.

Two invariants:
  1) Subprocess argv is exactly ``[<mnemonic_mcp_binary>, "mcp-stdio"]`` — no
     payload, no flags. The whole envelope flows over stdin.
  2) The stdin sequence is MCP JSON-RPC: first ``initialize``, then
     ``tools/call`` for ``mnemonic_sign_memory`` with the documented args.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.asyncio


class _FakeProc:
    """Stand-in for an ``asyncio.subprocess.Process`` with controllable I/O."""

    def __init__(self, init_response: bytes, sign_response: bytes) -> None:
        self._lines = [init_response, sign_response]
        self._stdin_bytes = bytearray()
        self.returncode: int | None = 0
        self.stdin = self
        self.stdout = self
        self.stderr = self
        self._stderr_bytes = b""

    # stdin
    def write(self, data: bytes) -> None:
        self._stdin_bytes.extend(data)

    async def drain(self) -> None:
        return

    def close(self) -> None:
        return

    async def wait_closed(self) -> None:
        return

    # stdout
    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self, n: int = -1) -> bytes:
        return self._stderr_bytes

    async def wait(self) -> int:
        return 0

    @property
    def stdin_payload(self) -> bytes:
        return bytes(self._stdin_bytes)

    def kill(self) -> None:
        self.returncode = -9


def _newline_jsonrpc(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


async def test_subprocess_argv_is_exactly_binary_and_mcp_stdio(
    monkeypatch,
) -> None:
    """No payload, no flags — strictly two argv elements."""
    from content_publisher import mcp_client

    init_resp = _newline_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "mnemonic", "version": "0.0"},
            },
        }
    )
    sign_resp = _newline_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"attestation_hash": "feedf00d" * 8, "ok": True}
                        ),
                    }
                ]
            },
        }
    )
    fake = _FakeProc(init_resp, sign_resp)

    captured_args: list = []

    async def fake_exec(*args, **kwargs):  # noqa: ANN002, ANN003
        captured_args.append((args, kwargs))
        return fake

    monkeypatch.setenv("MNEMONIK_MCP_BINARY", "/usr/local/bin/mnemonik-mcp")
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    hash_ = await mcp_client.sign_memory(
        content="body",
        content_sha256="a" * 64,
        post_url="https://t.me/c/1/2",
        score=85,
        prompt="brief",
    )

    assert hash_ == "feedf00d" * 8
    assert len(captured_args) == 1
    argv_args, argv_kwargs = captured_args[0]
    assert argv_args == ("/usr/local/bin/mnemonik-mcp", "mcp-stdio"), argv_args
    # env must be restricted (PATH only)
    env = argv_kwargs["env"]
    assert set(env.keys()) <= {"PATH"}


async def test_mcp_initialize_then_tools_call_over_stdin(monkeypatch) -> None:
    """stdin sequence: initialize, then tools/call mnemonic_sign_memory."""
    from content_publisher import mcp_client

    init_resp = _newline_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    )
    sign_resp = _newline_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"attestation_hash": "00" * 32}),
                    }
                ]
            },
        }
    )
    fake = _FakeProc(init_resp, sign_resp)

    async def fake_exec(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return fake

    monkeypatch.setenv("MNEMONIK_MCP_BINARY", "/usr/local/bin/mnemonik-mcp")
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await mcp_client.sign_memory(
        content="body bytes",
        content_sha256="b" * 64,
        post_url="https://t.me/c/9/9",
        score=92,
        prompt="brief text",
    )

    payload = fake.stdin_payload.decode("utf-8")
    lines = [ln for ln in payload.split("\n") if ln.strip()]
    assert len(lines) == 2, f"expected 2 JSON-RPC frames, got {len(lines)}: {lines!r}"

    init_msg = json.loads(lines[0])
    sign_msg = json.loads(lines[1])

    assert init_msg["method"] == "initialize"
    assert init_msg["params"]["clientInfo"]["name"] == "content-publisher"

    assert sign_msg["method"] == "tools/call"
    assert sign_msg["params"]["name"] == "mnemonic_sign_memory"
    args = sign_msg["params"]["arguments"]
    assert set(args.keys()) == {
        "content",
        "content_sha256",
        "post_url",
        "score",
        "prompt",
    }
    assert args["content"] == "body bytes"
    assert args["content_sha256"] == "b" * 64
    assert args["post_url"] == "https://t.me/c/9/9"
    assert args["score"] == 92
    assert args["prompt"] == "brief text"


async def test_mcp_failure_raises_mcpsignfailed(monkeypatch) -> None:
    """A JSON-RPC error response surfaces as ``MCPSignFailed``."""
    from content_publisher import mcp_client

    init_resp = _newline_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
    )
    err_resp = _newline_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "signer offline"}}
    )
    fake = _FakeProc(init_resp, err_resp)

    async def fake_exec(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return fake

    monkeypatch.setenv("MNEMONIK_MCP_BINARY", "/usr/local/bin/mnemonik-mcp")
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(mcp_client.MCPSignFailed):
        await mcp_client.sign_memory(
            content="x",
            content_sha256="c" * 64,
            post_url="https://t.me/c/1/3",
            score=80,
            prompt="brief",
        )
