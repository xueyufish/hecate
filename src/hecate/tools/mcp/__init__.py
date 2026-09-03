"""MCP (Model Context Protocol) client and server for Hecate."""

from __future__ import annotations

from hecate.tools.mcp.circuit_breaker import CircuitBreaker, CircuitState
from hecate.tools.mcp.client import HecateMCPClient
from hecate.tools.mcp.connection import MCPClientManager
from hecate.tools.mcp.errors import MCPConnectionError, MCPErrorCode
from hecate.tools.mcp.pool import ConnectionPool
from hecate.tools.mcp.registry import MCPServerRegistry
from hecate.tools.mcp.sync import MCPToolSync

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "ConnectionPool",
    "HecateMCPClient",
    "MCPClientManager",
    "MCPConnectionError",
    "MCPServerRegistry",
    "MCPErrorCode",
    "MCPToolSync",
]
