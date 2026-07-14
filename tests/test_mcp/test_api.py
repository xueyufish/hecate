"""Tests for MCP REST API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from hecate.services.mcp.connection import MCPClientManager


@pytest.fixture
def mcp_manager() -> MCPClientManager:
    """Create a fresh MCPClientManager for tests."""
    return MCPClientManager(health_check_interval=0)


async def test_list_connections_empty(client: AsyncClient) -> None:
    """List connections returns empty list when no servers registered."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        mock.return_value = manager
        resp = await client.get("/api/mcp/connections")
        assert resp.status_code == 200
        assert resp.json() == []


async def test_list_connections_with_servers(client: AsyncClient) -> None:
    """List connections returns registered servers with status."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        manager.register_server("srv1", "http://localhost:8001", "http", "ws-1")
        mock.return_value = manager

        resp = await client.get("/api/mcp/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "srv1"
        assert data[0]["registered"] is True


async def test_get_connection_detail(client: AsyncClient) -> None:
    """Get connection detail for a specific server."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        manager.register_server("srv1", "http://localhost:8001", "http")
        mock.return_value = manager

        resp = await client.get("/api/mcp/connections/srv1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "srv1"
        assert data["endpoint"] == "http://localhost:8001"


async def test_get_connection_not_found(client: AsyncClient) -> None:
    """Get connection returns 404 for unknown server."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        mock.return_value = manager

        resp = await client.get("/api/mcp/connections/unknown")
        assert resp.status_code == 404


async def test_reconnect_connection(client: AsyncClient) -> None:
    """Manual reconnect resets pool and circuit breaker."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        manager.register_server("srv1", "http://localhost:8001", "http")
        mock.return_value = manager

        resp = await client.post("/api/mcp/connections/srv1/reconnect")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reconnecting"


async def test_reconnect_connection_not_found(client: AsyncClient) -> None:
    """Reconnect returns 404 for unknown server."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        mock.return_value = manager

        resp = await client.post("/api/mcp/connections/unknown/reconnect")
        assert resp.status_code == 404


async def test_sync_connection(client: AsyncClient) -> None:
    """Sync invalidates cache and re-discovers tools."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        manager.register_server("srv1", "http://localhost:8001", "http")
        mock.return_value = manager

        # Mock discover_tools to avoid real connection
        manager.discover_tools = AsyncMock(return_value=[{"name": "tool1"}])  # type: ignore[method-assign]

        resp = await client.post("/api/mcp/connections/srv1/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "synced"
        assert data["tool_count"] == 1


async def test_sync_connection_not_found(client: AsyncClient) -> None:
    """Sync returns 404 for unknown server."""
    with patch("hecate.api.management.mcp.get_mcp_manager") as mock:
        manager = MCPClientManager(health_check_interval=0)
        mock.return_value = manager

        resp = await client.post("/api/mcp/connections/unknown/sync")
        assert resp.status_code == 404
