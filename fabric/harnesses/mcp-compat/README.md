# MCP-Compatibility Harness

## Purpose

Verifies that MCP surface changes (new methods, resource types, notifications) are backward-compatible with existing clients. Uses golden trace format to record and validate request/response pairs against the MCP specification.

## Fixture-Injection Contract

This harness records golden request/response traces from a reference MCP server and validates new implementations against them.

### How to Inject Real Fixtures

When `mnemonic-mcp` is available:

1. Copy canonical MCP server fixtures to `golden/`:
   - `golden/initialize.jsonl` — MCP initialize request/response
   - `golden/list-resources.jsonl` — resource enumeration traces
   - `golden/read-resource.jsonl` — individual resource fetch traces
   - `golden/call-tool.jsonl` — tool invocation traces

2. Update `client.py`:
   - Replace stub MCP client with real `mcp.Client` from `mcp-python-sdk`
   - Point to canonical server address (e.g., `stdio://mnemonic-mcp`)

3. Each harness run validates:
   - New client produces identical JSON-RPC messages to golden traces
   - All response fields match expected types and values
   - Backward compat: old traces still pass on new server

### Stub Fixture Format

Stub fixtures in `golden/stub.jsonl` are minimal MCP protocol traces:
```jsonl
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"harness","version":"0.1.0"}}}
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{},"serverInfo":{"name":"mnemonic-mcp-stub","version":"0.1.0"}}}
```

Each line is a sent message or received response. Mutating any byte causes test failure.

## How to Extend

### Add a New MCP Method

1. Update `client.py` to exercise new method signature
2. Record trace by running against real server:
   ```bash
   RECORD=1 python3 -m pytest tests/test_mcp_compat.py::test_new_method -v
   ```
3. Append recorded trace to `golden/new-method.jsonl`
4. Run test suite to validate round-trip
5. Commit fixture + updated snapshot

### Customize MCP Schema

1. Update proto definitions or JSON-RPC schema in mnemonic-mcp
2. Re-run golden trace capture
3. Diff new traces against old to detect breaking changes
4. Test against legacy clients if compat matrix requires it

## Build Instructions

```bash
make build          # Check MCP SDK availability
make test           # Run MCP compatibility tests
make verify         # Full conformance check with golden trace validation
make clean          # Remove test artifacts
```

## Troubleshooting

- **mcp-python-sdk missing**: Install via `pip install mcp` or run in placeholder mode
- **Golden trace mismatch**: Regenerate via `RECORD=1 pytest` against canonical server
- **Type errors in client**: Check MCP spec version in `initialize()` request
- **Timeout waiting for server**: Ensure MCP server is running on expected address

## Integration with PR Conformance

When `task_type: mcp-surface` is set in PR, `pr-conformance.yml` invokes:
```bash
cd fabric/harnesses/mcp-compat && make verify
```

Failure blocks PR merge until all MCP traces match golden.
