"""MCP client connection manager with pooling, circuit breaker, and lifecycle.

Rewritten to use ConnectionPool per server, CircuitBreaker per server,
MCPServerRegistry for registration, lazy connection on first tool call,
two-step probe (TCP → SDK handshake), auto-reconnection with exponential
backoff + jitter, per-request timeout, and health check background task.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from typing import Any
from urllib.parse import urlparse

from hecate.services.mcp.circuit_breaker import CircuitBreaker
from hecate.services.mcp.client import HecateMCPClient
from hecate.services.mcp.errors import MCPConnectionError, MCPErrorCode
from hecate.services.mcp.pool import ConnectionPool, PoolMetrics
from hecate.services.mcp.registry import MCPServerRegistry, ServerInfo

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manage connections to multiple MCP servers with pooling and resilience.

    Args:
        default_timeout: Default timeout in seconds for new client connections.
        pool_min_size: Minimum idle connections per server pool.
        pool_max_size: Maximum connections per server pool.
        borrow_timeout: Seconds to wait when pool is exhausted.
        health_check_interval: Seconds between periodic health checks.
        request_timeout: Per-request timeout in seconds.
        tool_cache_ttl: TTL in seconds for tool list cache.
        circuit_breaker_threshold: Consecutive failures before circuit opens.
        circuit_breaker_recovery_timeout: Seconds before half-open probe.
        reconnect_max_retries: Max reconnection attempts.
        reconnect_base_delay: Base delay for exponential backoff.
        reconnect_max_delay: Max delay for exponential backoff.
    """

    def __init__(
        self,
        default_timeout: int = 30,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        borrow_timeout: int = 5,
        health_check_interval: int = 30,
        request_timeout: int = 30,
        tool_cache_ttl: int = 300,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_recovery_timeout: int = 30,
        reconnect_max_retries: int = 5,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
    ) -> None:
        self._default_timeout = default_timeout
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._borrow_timeout = borrow_timeout
        self._health_check_interval = health_check_interval
        self._request_timeout = request_timeout
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_breaker_recovery_timeout = circuit_breaker_recovery_timeout
        self._reconnect_max_retries = reconnect_max_retries
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay

        self._registry = MCPServerRegistry(tool_cache_ttl=tool_cache_ttl)
        self._pools: dict[str, ConnectionPool] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._reconnecting: dict[str, bool] = {}

    @property
    def registry(self) -> MCPServerRegistry:
        """The MCP server registry."""
        return self._registry

    def register_server(
        self,
        name: str,
        endpoint: str,
        transport: str = "http",
        workspace_id: str | None = None,
    ) -> ServerInfo:
        """Register an MCP server without connecting (lazy connection).

        Args:
            name: Unique server name.
            endpoint: Server endpoint URL or command.
            transport: Transport type ('http' or 'stdio').
            workspace_id: Optional workspace ID for multi-tenant isolation.

        Returns:
            Registered ServerInfo.
        """
        info = self._registry.register(name, endpoint, transport, workspace_id)
        self._circuit_breakers[name] = CircuitBreaker(
            failure_threshold=self._circuit_breaker_threshold,
            recovery_timeout=self._circuit_breaker_recovery_timeout,
        )
        return info

    def unregister_server(self, name: str) -> ServerInfo | None:
        """Unregister an MCP server and clean up its pool and circuit breaker.

        Args:
            name: Server name.

        Returns:
            Unregistered ServerInfo, or None if not found.
        """
        info = self._registry.unregister(name)
        if info:
            self._circuit_breakers.pop(name, None)
            self._reconnecting.pop(name, None)
            pool = self._pools.pop(name, None)
            if pool is not None:
                asyncio.create_task(pool.close())
        return info

    def get_server_info(self, name: str) -> ServerInfo | None:
        """Get registered server info by name."""
        return self._registry.get_server(name)

    def list_servers(self) -> list[ServerInfo]:
        """List all registered servers."""
        return self._registry.list_servers()

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call a tool on an MCP server (lazy connection, circuit breaker, timeout).

        Creates connection on first call, reuses session for subsequent calls.
        Enforces per-request timeout and circuit breaker.

        Args:
            server_name: Registered server name.
            tool_name: Tool name.
            arguments: Tool arguments.

        Returns:
            Tool execution result.

        Raises:
            MCPConnectionError: On connection failure, pool exhaustion, timeout, or circuit open.
        """
        # Check circuit breaker
        cb = self._circuit_breakers.get(server_name)
        if cb and not cb.can_proceed():
            raise MCPConnectionError(
                MCPErrorCode.MCP_CIRCUIT_OPEN,
                f"Circuit breaker is open for server '{server_name}'",
                details={"server": server_name, "state": cb.state.value},
            )

        # Get or create connection pool
        pool = await self._get_or_create_pool(server_name)

        # Borrow connection from pool
        client = await pool.borrow()

        try:
            # Execute with per-request timeout
            result = await asyncio.wait_for(
                client.call_tool(tool_name, arguments),
                timeout=self._request_timeout,
            )
            if cb:
                cb.record_success()
            return result

        except TimeoutError:
            if cb:
                cb.record_failure()
            raise MCPConnectionError(
                MCPErrorCode.MCP_REQUEST_TIMEOUT,
                f"Request to '{server_name}' timed out after {self._request_timeout}s",
                details={"server": server_name, "timeout": str(self._request_timeout)},
            ) from None

        except Exception:
            if cb:
                cb.record_failure()
            raise

        finally:
            await pool.return_client(client)

    async def discover_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """Discover tools from registered servers with caching.

        Args:
            server_name: Optional server name filter. If None, discovers from all.

        Returns:
            Aggregated tool list, each tagged with ``mcp_server`` key.
        """
        if server_name is not None:
            servers = [s for s in self._registry.list_servers() if s.name == server_name]
        else:
            servers = self._registry.list_servers()

        all_tools: list[dict[str, Any]] = []
        for server in servers:
            try:
                tools = await self._discover_tools_from_server(server.name)
                for tool in tools:
                    tool["mcp_server"] = server.name
                all_tools.extend(tools)
            except Exception:
                logger.error(
                    "Failed to discover tools from MCP server '%s'",
                    server.name,
                    exc_info=True,
                )
        return all_tools

    async def _discover_tools_from_server(self, name: str) -> list[dict[str, Any]]:
        """Discover tools from a single server with single-flight caching."""
        pool = await self._get_or_create_pool(name)

        async def _list_tools() -> list[dict[str, Any]]:
            client = await pool.borrow()
            try:
                return await client.list_tools()
            finally:
                await pool.return_client(client)

        return await self._registry.discover_tools(name, _list_tools)

    async def _get_or_create_pool(self, name: str) -> ConnectionPool:
        """Get existing pool or create a new one with lazy connection."""
        if name in self._pools:
            return self._pools[name]

        info = self._registry.get_server(name)
        if info is None:
            raise MCPConnectionError(
                MCPErrorCode.MCP_CONNECTION_FAILED,
                f"MCP server '{name}' is not registered",
                details={"server": name},
            )

        pool = ConnectionPool(
            server_name=name,
            min_size=self._pool_min_size,
            max_size=self._pool_max_size,
            borrow_timeout=self._borrow_timeout,
            health_check_interval=self._health_check_interval,
            connect_factory=lambda: self._create_client(info),
        )
        self._pools[name] = pool
        await pool.start_health_checks()
        return pool

    async def _create_client(self, info: ServerInfo) -> HecateMCPClient:
        """Create a new MCP client with two-step probe."""
        # Two-step probe
        await self._tcp_probe(info.endpoint, info.transport)

        client = HecateMCPClient(timeout=self._default_timeout)
        try:
            if info.transport == "http":
                await client.connect_http(info.endpoint)
            elif info.transport == "stdio":
                await client.connect_stdio(command=info.endpoint, args=[])
            else:
                raise MCPConnectionError(
                    MCPErrorCode.MCP_CONNECTION_FAILED,
                    f"Unsupported transport: {info.transport}",
                )
        except MCPConnectionError:
            raise
        except Exception as e:
            raise MCPConnectionError(
                MCPErrorCode.MCP_CONNECTION_FAILED,
                f"SDK handshake failed for '{info.name}': {e}",
                details={"server": info.name, "endpoint": info.endpoint},
            ) from e

        logger.info("MCP client created for '%s' (%s)", info.name, info.transport)
        return client

    async def _tcp_probe(self, endpoint: str, transport: str) -> None:
        """Two-step probe: TCP reachability check.

        Args:
            endpoint: Server endpoint URL.
            transport: Transport type.

        Raises:
            MCPConnectionError: On DNS, timeout, port closed, SSL errors.
        """
        if transport != "http":
            return  # No TCP probe for stdio

        parsed = urlparse(endpoint)
        hostname = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # DNS resolution
        try:
            await asyncio.get_event_loop().run_in_executor(None, socket.getaddrinfo, hostname, port)
        except socket.gaierror as e:
            raise MCPConnectionError(
                MCPErrorCode.MCP_DNS_FAILURE,
                f"DNS resolution failed for '{hostname}': {e}",
                details={"hostname": hostname},
            ) from e

        # TCP connectivity
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(hostname, port),
                timeout=self._default_timeout,
            )
            writer.close()
            await writer.wait_closed()
        except TimeoutError:
            raise MCPConnectionError(
                MCPErrorCode.MCP_CONNECT_TIMEOUT,
                f"TCP connection to {hostname}:{port} timed out after {self._default_timeout}s",
                details={"hostname": hostname, "port": str(port), "timeout": str(self._default_timeout)},
            ) from None
        except ConnectionRefusedError as e:
            raise MCPConnectionError(
                MCPErrorCode.MCP_PORT_CLOSED,
                f"TCP connection refused at {hostname}:{port}",
                details={"hostname": hostname, "port": str(port)},
            ) from e
        except OSError as e:
            if "SSL" in str(e) or "CERTIFICATE" in str(e).upper():
                raise MCPConnectionError(
                    MCPErrorCode.MCP_SSL_ERROR,
                    f"SSL error connecting to {hostname}:{port}: {e}",
                    details={"hostname": hostname, "port": str(port)},
                ) from e
            # asyncio wraps ConnectionRefusedError in OSError on some platforms
            if "Connect call failed" in str(e) or "Errno 61" in str(e):
                raise MCPConnectionError(
                    MCPErrorCode.MCP_PORT_CLOSED,
                    f"TCP connection refused at {hostname}:{port}: {e}",
                    details={"hostname": hostname, "port": str(port)},
                ) from e
            raise

    async def reconnect(self, name: str) -> None:
        """Manually trigger reconnection for a server.

        Drops existing connections and creates a new one.

        Args:
            name: Server name.
        """
        pool = self._pools.get(name)
        if pool:
            await pool.close()
            self._pools.pop(name, None)

        # Reset circuit breaker
        cb = self._circuit_breakers.get(name)
        if cb:
            cb.reset()

        # Invalidate tool cache
        self._registry.invalidate_cache(name)

        logger.info("Manual reconnect triggered for '%s'", name)

    async def _auto_reconnect(self, name: str) -> None:
        """Auto-reconnect with exponential backoff + jitter.

        Args:
            name: Server name.
        """
        if self._reconnecting.get(name, False):
            return  # Already reconnecting

        self._reconnecting[name] = True
        try:
            for attempt in range(self._reconnect_max_retries):
                delay = min(
                    self._reconnect_base_delay * (2**attempt) + random.uniform(0, 1),  # noqa: S311
                    self._reconnect_max_delay,
                )
                logger.info(
                    "Reconnecting to '%s' (attempt %d/%d, delay=%.1fs)",
                    name,
                    attempt + 1,
                    self._reconnect_max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

                # Reset pool and try fresh connection
                pool = self._pools.pop(name, None)
                if pool:
                    await pool.close()

                try:
                    new_pool = await self._get_or_create_pool(name)
                    client = await new_pool.borrow()
                    await new_pool.return_client(client)
                    logger.info("Reconnection to '%s' succeeded", name)
                    return
                except Exception:
                    logger.warning("Reconnection attempt %d failed for '%s'", attempt + 1, name)

            # All retries exhausted
            cb = self._circuit_breakers.get(name)
            if cb:
                for _ in range(self._reconnect_max_retries):
                    cb.record_failure()
            logger.error("All reconnection attempts exhausted for '%s'", name)
        finally:
            self._reconnecting[name] = False

    async def get_connection_status(self, name: str) -> dict[str, Any]:
        """Get detailed connection status for a server.

        Args:
            name: Server name.

        Returns:
            Status dict with connection state, pool metrics, tool count.
        """
        info = self._registry.get_server(name)
        if info is None:
            return {"registered": False}

        pool = self._pools.get(name)
        cb = self._circuit_breakers.get(name)
        cached_tools = self._registry.get_cached_tools(name)

        pool_metrics = pool.metrics if pool else PoolMetrics()

        return {
            "registered": True,
            "name": name,
            "endpoint": info.endpoint,
            "transport": info.transport,
            "workspace_id": info.workspace_id,
            "circuit_state": cb.state.value if cb else "closed",
            "reconnecting": self._reconnecting.get(name, False),
            "pool": {
                "active": pool_metrics.active,
                "idle": pool_metrics.idle,
                "total": pool_metrics.total,
                "max": pool_metrics.max,
                "healthy": pool_metrics.healthy,
            },
            "tool_count": len(cached_tools) if cached_tools is not None else 0,
            "tools_cached": cached_tools is not None,
        }

    async def disconnect_all(self) -> None:
        """Disconnect from all servers and clean up resources."""
        for name, pool in self._pools.items():
            try:
                await pool.close()
            except Exception:
                logger.error("Error disconnecting pool '%s'", name, exc_info=True)
        self._pools.clear()
        self._circuit_breakers.clear()
        self._reconnecting.clear()
