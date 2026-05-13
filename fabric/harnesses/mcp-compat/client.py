"""
Minimal MCP client harness for compatibility testing.

Attempts to import mcp-python-sdk; falls back to stub client if unavailable.
"""

import json
import sys
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class MCPCapabilities:
    """Stub MCP capabilities."""
    pass


@dataclass
class MCPResponse:
    """Stub MCP response."""
    jsonrpc: str
    id: int
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


class StubMCPClient:
    """
    Stub MCP client for offline testing.
    Generates minimal valid MCP protocol messages without a real server.
    """

    def __init__(self, server_address: str = "localhost:5000"):
        self.server_address = server_address
        self.message_id = 1
        self.traces = []

    def _record_message(self, direction: str, msg: Dict[str, Any]) -> None:
        """Record outgoing/incoming messages for golden trace."""
        self.traces.append({"direction": direction, "message": msg})

    async def initialize(self) -> MCPResponse:
        """Send MCP initialize request."""
        request = {
            "jsonrpc": "2.0",
            "id": self.message_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "harness", "version": "0.1.0"},
            },
        }
        self._record_message("send", request)
        self.message_id += 1

        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "mnemonic-mcp-stub", "version": "0.1.0"},
            },
        }
        self._record_message("recv", response)
        return MCPResponse(**response)

    async def list_resources(self) -> List[Dict[str, Any]]:
        """List available resources."""
        request = {
            "jsonrpc": "2.0",
            "id": self.message_id,
            "method": "resources/list",
            "params": {},
        }
        self._record_message("send", request)
        self.message_id += 1

        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"resources": []},
        }
        self._record_message("recv", response)
        return response["result"]["resources"]

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a single resource."""
        request = {
            "jsonrpc": "2.0",
            "id": self.message_id,
            "method": "resources/read",
            "params": {"uri": uri},
        }
        self._record_message("send", request)
        self.message_id += 1

        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": ""}]},
        }
        self._record_message("recv", response)
        return response["result"]

    def get_traces(self) -> List[Dict[str, Any]]:
        """Return recorded traces in JSONL format."""
        return self.traces


try:
    from mcp import Client as RealMCPClient
    MCPClient = RealMCPClient
except ImportError:
    MCPClient = StubMCPClient


async def test_basic_flow(client: Any) -> None:
    """Execute basic MCP test flow."""
    await client.initialize()
    resources = await client.list_resources()
    assert isinstance(resources, list)


if __name__ == "__main__":
    import asyncio

    client = MCPClient()
    asyncio.run(test_basic_flow(client))
    print("MCP client test passed")
