"""
fabric.integrations
====================
Adapters connecting molyanov skill commands to external service APIs.

Public surface:
    SwarmBridge  — adapter from /do-feature to ruflo swarm MCP
"""

from fabric.integrations.swarm_bridge import SwarmBridge

__all__ = ["SwarmBridge"]
