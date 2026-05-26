"""Tests for MCP integration components.

Tests cover:
- MCP client connection
- Tool discovery
- Tool execution
- Tool synchronization
"""

from __future__ import annotations

import pytest

from hecate.services.mcp.client import MCPClient, MCPManager, mcp_manager
from hecate.services.mcp.sync import mcp_tool_sync


@pytest.mark.asyncio
async def test_mcp_client_connect() -> None:
    """Test MCP client connection."""
    client = MCPClient("http://localhost:8080")
    result = await client.connect()
    assert result is True
    assert client._connected is True


@pytest.mark.asyncio
async def test_mcp_client_list_tools() -> None:
    """Test MCP client tool listing."""
    client = MCPClient("http://localhost:8080")
    await client.connect()
    tools = await client.list_tools()
    assert isinstance(tools, list)


@pytest.mark.asyncio
async def test_mcp_client_call_tool() -> None:
    """Test MCP client tool execution."""
    client = MCPClient("http://localhost:8080")
    await client.connect()
    result = await client.call_tool("test_tool", {"arg": "value"})
    assert result["success"] is True


@pytest.mark.asyncio
async def test_mcp_manager_add_server() -> None:
    """Test MCP manager server addition."""
    manager = MCPManager()
    client = await manager.add_server("http://localhost:8080")
    assert client is not None
    assert "http://localhost:8080" in manager._clients


@pytest.mark.asyncio
async def test_mcp_manager_discover_tools() -> None:
    """Test MCP manager tool discovery."""
    manager = MCPManager()
    await manager.add_server("http://localhost:8080")
    tools = await manager.discover_tools()
    assert isinstance(tools, list)


@pytest.mark.asyncio
async def test_mcp_tool_sync() -> None:
    """Test MCP tool synchronization."""
    tools = await mcp_tool_sync.sync_tools("http://localhost:8080")
    assert isinstance(tools, list)
