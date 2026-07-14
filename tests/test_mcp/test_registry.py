"""Tests for MCP MCPServerRegistry."""

from __future__ import annotations

import asyncio
import time

from hecate.services.mcp.registry import MCPServerRegistry


def test_register_and_list() -> None:
    """Register adds server, list returns all registered servers."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http", "ws-1")
    registry.register("srv2", "http://localhost:8002", "http", "ws-2")

    servers = registry.list_servers()
    assert len(servers) == 2
    names = {s.name for s in servers}
    assert names == {"srv1", "srv2"}


def test_unregister() -> None:
    """Unregister removes server and clears cache."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http")
    registry.cache_tools("srv1", [{"name": "tool1"}])

    result = registry.unregister("srv1")
    assert result is not None
    assert result.name == "srv1"
    assert registry.get_server("srv1") is None
    assert registry.get_cached_tools("srv1") is None


def test_unregister_nonexistent() -> None:
    """Unregister returns None for unknown server."""
    registry = MCPServerRegistry()
    assert registry.unregister("unknown") is None


def test_get_server() -> None:
    """Get server returns ServerInfo for registered server."""
    registry = MCPServerRegistry()
    registry.register("srv1", "http://localhost:8001", "http", "ws-1")

    info = registry.get_server("srv1")
    assert info is not None
    assert info.name == "srv1"
    assert info.endpoint == "http://localhost:8001"
    assert info.transport == "http"
    assert info.workspace_id == "ws-1"


def test_has_server() -> None:
    """Has server checks registration status."""
    registry = MCPServerRegistry()
    registry.register("srv1", "http://localhost:8001", "http")

    assert registry.has_server("srv1") is True
    assert registry.has_server("unknown") is False


def test_cache_and_retrieve_tools() -> None:
    """Cache tools and retrieve them while fresh."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http")

    tools = [{"name": "tool1", "description": "A tool"}]
    registry.cache_tools("srv1", tools)

    cached = registry.get_cached_tools("srv1")
    assert cached == tools


def test_cache_expired() -> None:
    """Expired cache returns None."""
    registry = MCPServerRegistry(tool_cache_ttl=0.01)
    registry.register("srv1", "http://localhost:8001", "http")

    registry.cache_tools("srv1", [{"name": "tool1"}])
    time.sleep(0.02)

    assert registry.get_cached_tools("srv1") is None


def test_invalidate_cache() -> None:
    """Invalidate cache removes cached tools."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http")
    registry.cache_tools("srv1", [{"name": "tool1"}])

    registry.invalidate_cache("srv1")
    assert registry.get_cached_tools("srv1") is None


async def test_discover_tools_cache_hit() -> None:
    """Discover tools returns cached result on cache hit."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http")
    registry.cache_tools("srv1", [{"name": "cached_tool"}])

    call_count = 0

    async def fake_list_tools() -> list:
        nonlocal call_count
        call_count += 1
        return [{"name": "fresh_tool"}]

    tools = await registry.discover_tools("srv1", fake_list_tools)
    assert tools == [{"name": "cached_tool"}]
    assert call_count == 0  # Factory not called — cache hit


async def test_discover_tools_cache_miss() -> None:
    """Discover tools calls factory on cache miss."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http")

    async def fake_list_tools() -> list:
        return [{"name": "fresh_tool"}]

    tools = await registry.discover_tools("srv1", fake_list_tools)
    assert tools == [{"name": "fresh_tool"}]

    # Should be cached now
    cached = registry.get_cached_tools("srv1")
    assert cached == [{"name": "fresh_tool"}]


async def test_single_flight_refresh() -> None:
    """Concurrent cache misses trigger only one factory call."""
    registry = MCPServerRegistry(tool_cache_ttl=60)
    registry.register("srv1", "http://localhost:8001", "http")

    call_count = 0

    async def slow_list_tools() -> list:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return [{"name": "tool1"}]

    # Launch multiple concurrent discovers
    results = await asyncio.gather(
        registry.discover_tools("srv1", slow_list_tools),
        registry.discover_tools("srv1", slow_list_tools),
        registry.discover_tools("srv1", slow_list_tools),
    )

    # All should get the same result
    assert all(r == [{"name": "tool1"}] for r in results)
    # Factory should be called only once
    assert call_count == 1
