"""MCP Server Registry — capability discovery and tool caching.

Maintains a registry of MCP servers with their capabilities (tools/resources/prompts).
Supports TTL-based tool list caching with single-flight refresh.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServerInfo:
    """Information about a registered MCP server."""

    name: str
    """Unique server name."""

    endpoint: str
    """Server endpoint URL or command."""

    transport: str
    """Transport type: 'http' or 'stdio'."""

    workspace_id: str | None = None
    """Owning workspace ID for multi-tenant isolation."""

    registered_at: float = field(default_factory=time.monotonic)
    """Monotonic timestamp of registration."""


@dataclass
class ToolCacheEntry:
    """Cached tool list with TTL tracking."""

    tools: list[dict[str, Any]]
    """Cached tool list."""

    cached_at: float
    """Monotonic timestamp of cache write."""

    ttl: float
    """Cache TTL in seconds."""

    @property
    def expired(self) -> bool:
        """Whether the cache entry has expired."""
        return (time.monotonic() - self.cached_at) >= self.ttl


class MCPServerRegistry:
    """Registry of MCP servers with capability discovery and tool caching.

    Args:
        tool_cache_ttl: Default TTL in seconds for tool list cache entries.
    """

    def __init__(self, tool_cache_ttl: int = 300) -> None:
        self._tool_cache_ttl = tool_cache_ttl
        self._servers: dict[str, ServerInfo] = {}
        self._tool_cache: dict[str, ToolCacheEntry] = {}
        self._single_flight_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def register(
        self,
        name: str,
        endpoint: str,
        transport: str = "http",
        workspace_id: str | None = None,
    ) -> ServerInfo:
        """Register an MCP server (no connection created).

        Args:
            name: Unique server name.
            endpoint: Server endpoint URL or command.
            transport: Transport type ('http' or 'stdio').
            workspace_id: Optional workspace ID for multi-tenant isolation.

        Returns:
            The registered ServerInfo.
        """
        info = ServerInfo(
            name=name,
            endpoint=endpoint,
            transport=transport,
            workspace_id=workspace_id,
        )
        self._servers[name] = info
        logger.info("MCP server '%s' registered (%s)", name, transport)
        return info

    def unregister(self, name: str) -> ServerInfo | None:
        """Unregister an MCP server and clear its cached tools.

        Args:
            name: Server name to unregister.

        Returns:
            The unregistered ServerInfo, or None if not found.
        """
        info = self._servers.pop(name, None)
        if info:
            self._tool_cache.pop(name, None)
            self._single_flight_locks.pop(name, None)
            logger.info("MCP server '%s' unregistered", name)
        return info

    def get_server(self, name: str) -> ServerInfo | None:
        """Get server info by name.

        Args:
            name: Server name.

        Returns:
            ServerInfo if registered, None otherwise.
        """
        return self._servers.get(name)

    def list_servers(self) -> list[ServerInfo]:
        """List all registered servers.

        Returns:
            List of all registered ServerInfo.
        """
        return list(self._servers.values())

    def has_server(self, name: str) -> bool:
        """Check if a server is registered.

        Args:
            name: Server name.

        Returns:
            True if registered.
        """
        return name in self._servers

    def get_cached_tools(self, name: str) -> list[dict[str, Any]] | None:
        """Get cached tool list for a server if not expired.

        Args:
            name: Server name.

        Returns:
            Cached tool list if available and fresh, None otherwise.
        """
        entry = self._tool_cache.get(name)
        if entry is None or entry.expired:
            return None
        return entry.tools

    def cache_tools(self, name: str, tools: list[dict[str, Any]]) -> None:
        """Cache a tool list for a server.

        Args:
            name: Server name.
            tools: Tool list to cache.
        """
        self._tool_cache[name] = ToolCacheEntry(
            tools=tools,
            cached_at=time.monotonic(),
            ttl=self._tool_cache_ttl,
        )

    def invalidate_cache(self, name: str) -> None:
        """Invalidate cached tools for a server.

        Args:
            name: Server name.
        """
        self._tool_cache.pop(name, None)

    async def discover_tools(
        self,
        name: str,
        list_tools_fn: Any,
    ) -> list[dict[str, Any]]:
        """Discover tools from a server with single-flight refresh.

        On cache miss, a single ``list_tools`` call is made. Concurrent
        requests for the same server wait for the first call to complete.

        Args:
            name: Server name.
            list_tools_fn: Async callable that returns the tool list.

        Returns:
            Tool list from cache or fresh call.
        """
        # Check cache first
        cached = self.get_cached_tools(name)
        if cached is not None:
            return cached

        # Single-flight: acquire per-server lock
        async with self._global_lock:
            if name not in self._single_flight_locks:
                self._single_flight_locks[name] = asyncio.Lock()

        async with self._single_flight_locks[name]:
            # Double-check cache (another call may have refreshed it)
            cached = self.get_cached_tools(name)
            if cached is not None:
                return cached

            # Fresh call
            tools = await list_tools_fn()
            self.cache_tools(name, tools)
            return tools
