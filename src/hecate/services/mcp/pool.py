"""Connection pool for MCP server connections.

Provides per-server connection pooling with borrow/return semantics,
health check background task, and pool metrics.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from hecate.services.mcp.client import HecateMCPClient
from hecate.services.mcp.errors import MCPConnectionError, MCPErrorCode

logger = logging.getLogger(__name__)


@dataclass
class PoolMetrics:
    """Connection pool metrics snapshot."""

    active: int = 0
    """Connections currently borrowed and in use."""

    idle: int = 0
    """Connections available in the pool."""

    total: int = 0
    """Total connections managed by this pool (active + idle)."""

    max: int = 5
    """Maximum pool capacity."""

    healthy: bool = True
    """Whether the pool's connections are considered healthy."""


@dataclass
class _PoolEntry:
    """Internal pool entry wrapping a client."""

    client: HecateMCPClient
    in_use: bool = False
    consecutive_health_failures: int = 0


class ConnectionPool:
    """Per-MCP-server connection pool.

    Supports borrow-with-timeout and return semantics. For stdio transport,
    pool is capped at 1 (single connection, not poolable).

    Args:
        server_name: Name of the MCP server this pool serves.
        min_size: Minimum number of idle connections to maintain.
        max_size: Maximum number of connections (pool capacity).
        borrow_timeout: Seconds to wait when pool is exhausted before failing.
        health_check_interval: Seconds between periodic health checks (0 to disable).
        connect_factory: Async callable that creates a new HecateMCPClient.
    """

    def __init__(
        self,
        server_name: str,
        min_size: int = 1,
        max_size: int = 5,
        borrow_timeout: int = 5,
        health_check_interval: int = 30,
        connect_factory: Any = None,
    ) -> None:
        self._server_name = server_name
        self._min_size = min_size
        self._max_size = max_size
        self._borrow_timeout = borrow_timeout
        self._health_check_interval = health_check_interval
        self._connect_factory = connect_factory

        self._entries: list[_PoolEntry] = []
        self._lock = asyncio.Lock()
        self._borrow_event = asyncio.Event()
        self._health_check_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def metrics(self) -> PoolMetrics:
        """Current pool metrics snapshot."""
        active = sum(1 for e in self._entries if e.in_use)
        idle = sum(1 for e in self._entries if not e.in_use)
        healthy = all(e.consecutive_health_failures < 3 for e in self._entries)
        return PoolMetrics(
            active=active,
            idle=idle,
            total=active + idle,
            max=self._max_size,
            healthy=healthy,
        )

    async def borrow(self) -> HecateMCPClient:
        """Borrow a connection from the pool.

        Returns an idle connection if available, creates a new one if pool
        is below max, or waits up to borrow_timeout if pool is exhausted.

        Returns:
            A connected HecateMCPClient.

        Raises:
            MCPConnectionError: If pool is exhausted after timeout.
        """
        if self._closed:
            raise MCPConnectionError(
                MCPErrorCode.MCP_CONNECTION_FAILED,
                f"Pool for '{self._server_name}' is closed",
            )

        deadline = asyncio.get_event_loop().time() + self._borrow_timeout

        while True:
            async with self._lock:
                # Try to find an idle connection
                for entry in self._entries:
                    if not entry.in_use and entry.client.connected:
                        entry.in_use = True
                        return entry.client

                # Create new connection if below max
                if len(self._entries) < self._max_size:
                    client = await self._create_connection()
                    entry = _PoolEntry(client=client, in_use=True)
                    self._entries.append(entry)
                    return client

            # Pool exhausted — wait for a return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise MCPConnectionError(
                    MCPErrorCode.MCP_POOL_EXHAUSTED,
                    f"Connection pool for '{self._server_name}' exhausted (max={self._max_size}, all in use)",
                    details={"server": self._server_name, "max_size": str(self._max_size)},
                )

            try:
                await asyncio.wait_for(self._borrow_event.wait(), timeout=remaining)
            except TimeoutError:
                raise MCPConnectionError(
                    MCPErrorCode.MCP_POOL_EXHAUSTED,
                    f"Connection pool for '{self._server_name}' exhausted after {self._borrow_timeout}s timeout",
                    details={"server": self._server_name, "timeout": str(self._borrow_timeout)},
                ) from None
            self._borrow_event.clear()

    async def return_client(self, client: HecateMCPClient) -> None:
        """Return a connection to the pool.

        Args:
            client: The client to return.
        """
        async with self._lock:
            for entry in self._entries:
                if entry.client is client:
                    entry.in_use = False
                    self._borrow_event.set()
                    return

    async def remove_client(self, client: HecateMCPClient) -> None:
        """Remove a specific client from the pool (e.g. after connection failure).

        Args:
            client: The client to remove.
        """
        async with self._lock:
            self._entries = [e for e in self._entries if e.client is not client]
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Error disconnecting removed client", exc_info=True)

    async def _create_connection(self) -> HecateMCPClient:
        """Create a new connection using the factory."""
        if self._connect_factory is None:
            raise MCPConnectionError(
                MCPErrorCode.MCP_CONNECTION_FAILED,
                f"No connect factory configured for pool '{self._server_name}'",
            )
        return await self._connect_factory()

    async def start_health_checks(self) -> None:
        """Start the periodic health check background task."""
        if self._health_check_interval <= 0 or self._closed:
            return
        if self._health_check_task is not None:
            return
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(),
            name=f"mcp-health-{self._server_name}",
        )

    async def stop_health_checks(self) -> None:
        """Stop the health check background task."""
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_check_task
            self._health_check_task = None

    async def _health_check_loop(self) -> None:
        """Periodic health check loop."""
        while not self._closed:
            await asyncio.sleep(self._health_check_interval)
            if self._closed:
                break

            async with self._lock:
                for entry in self._entries:
                    if entry.in_use or not entry.client.connected:
                        continue
                    try:
                        await entry.client.health_check()
                        entry.consecutive_health_failures = 0
                    except Exception:
                        entry.consecutive_health_failures += 1
                        logger.warning(
                            "Health check failed for '%s' (%d/3 consecutive failures)",
                            self._server_name,
                            entry.consecutive_health_failures,
                        )
                        if entry.consecutive_health_failures >= 3:
                            logger.error(
                                "Connection to '%s' marked unhealthy after 3 consecutive failures",
                                self._server_name,
                            )

    async def close(self) -> None:
        """Close all connections and stop health checks."""
        self._closed = True
        await self.stop_health_checks()
        async with self._lock:
            for entry in self._entries:
                try:
                    await entry.client.disconnect()
                except Exception:
                    logger.debug("Error disconnecting client during pool close", exc_info=True)
            self._entries.clear()
