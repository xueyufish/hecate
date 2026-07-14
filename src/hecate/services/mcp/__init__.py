"""MCP (Model Context Protocol) client and server for Hecate."""

from __future__ import annotations

from hecate.services.mcp.circuit_breaker import CircuitBreaker, CircuitState
from hecate.services.mcp.client import HecateMCPClient
from hecate.services.mcp.connection import MCPClientManager
from hecate.services.mcp.errors import MCPConnectionError, MCPErrorCode
from hecate.services.mcp.pool import ConnectionPool
from hecate.services.mcp.registry import MCPServerRegistry
from hecate.services.mcp.sync import MCPToolSync

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
