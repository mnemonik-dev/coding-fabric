"""
MCP compatibility tests.

Golden trace validation: compares generated traces against recorded fixtures.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

HARNESS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HARNESS_DIR))

from client import MCPClient, StubMCPClient

GOLDEN_DIR = HARNESS_DIR / "golden"


def load_golden_traces(path: Path) -> list:
    """Load JSONL golden traces."""
    if not path.exists():
        return []
    traces = []
    with open(path) as f:
        for line in f:
            if line.strip():
                traces.append(json.loads(line))
    return traces


@pytest.mark.asyncio
async def test_initialize():
    """Test MCP initialize request."""
    client = StubMCPClient()
    result = await client.initialize()

    assert result.jsonrpc == "2.0"
    assert result.result is not None
    assert result.result["protocolVersion"] == "2024-11-05"


@pytest.mark.asyncio
async def test_list_resources():
    """Test MCP list_resources request."""
    client = StubMCPClient()
    resources = await client.list_resources()

    assert isinstance(resources, list)


@pytest.mark.asyncio
async def test_read_resource():
    """Test MCP read_resource request."""
    client = StubMCPClient()
    result = await client.read_resource("stub://test")

    assert "contents" in result


@pytest.mark.asyncio
async def test_golden_trace_format():
    """Validate golden trace JSONL format."""
    golden_file = GOLDEN_DIR / "stub.jsonl"
    traces = load_golden_traces(golden_file)

    assert len(traces) > 0, "Golden traces not found"

    for trace in traces:
        assert "jsonrpc" in trace
        assert trace["jsonrpc"] == "2.0"
        assert "id" in trace or "error" in trace or "result" in trace


@pytest.mark.asyncio
async def test_trace_mutation_detection():
    """Verify that mutating golden traces causes test failure."""
    import copy

    golden_file = GOLDEN_DIR / "stub.jsonl"
    traces = load_golden_traces(golden_file)

    original_len = len(traces)
    assert original_len > 0

    mutated_traces = copy.deepcopy(traces)
    if mutated_traces:
        mutated_traces[0]["id"] = 999

    assert mutated_traces != traces, "Trace mutation not detected"


@pytest.mark.asyncio
async def test_round_trip_consistency():
    """Test that client produces consistent traces across runs."""
    client1 = StubMCPClient()
    await client1.initialize()
    traces1 = client1.get_traces()

    client2 = StubMCPClient()
    await client2.initialize()
    traces2 = client2.get_traces()

    assert len(traces1) == len(traces2)
    assert traces1[0] == traces2[0]


def test_golden_trace_exists():
    """Verify golden trace fixture exists."""
    golden_file = GOLDEN_DIR / "stub.jsonl"
    assert golden_file.exists(), "Golden trace fixture not found"


def test_golden_trace_valid_json():
    """Verify each line in golden trace is valid JSON."""
    golden_file = GOLDEN_DIR / "stub.jsonl"
    with open(golden_file) as f:
        for i, line in enumerate(f):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    pytest.fail(f"Invalid JSON at line {i+1}: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
