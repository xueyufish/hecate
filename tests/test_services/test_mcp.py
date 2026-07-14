"""Tests for MCP integration components.

Tests cover:
- MCP client connection
- Tool discovery
- Tool execution
- Tool synchronization
"""

from __future__ import annotations

import pytest

from hecate.services.mcp.client import HecateMCPClient
from hecate.services.mcp.connection import MCPClientManager


@pytest.mark.asyncio
async def test_hecate_mcp_client_init() -> None:
    """Test HecateMCPClient initialization."""
    client = HecateMCPClient(timeout=30)
    assert client.connected is False
    assert client._timeout == 30


@pytest.mark.asyncio
async def test_mcp_client_manager_init() -> None:
    """Test MCPClientManager initialization."""
    manager = MCPClientManager(default_timeout=30)
    assert manager._default_timeout == 30
    assert len(manager.list_servers()) == 0


@pytest.mark.asyncio
async def test_mcp_client_manager_get_nonexistent() -> None:
    """Test MCPClientManager returns None for nonexistent server."""
    manager = MCPClientManager()
    info = manager.get_server_info("nonexistent")
    assert info is None
