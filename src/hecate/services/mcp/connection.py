"""MCP client connection manager for multiple server connections."""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.mcp.client import HecateMCPClient

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manage connections to multiple MCP servers.

    Args:
        default_timeout: Default timeout in seconds for new client connections.
    """

    def __init__(self, default_timeout: int = 30) -> None:
        self._default_timeout = default_timeout
        self._clients: dict[str, HecateMCPClient] = {}

    async def add_server(
        self,
        name: str,
        server_url: str,
        transport: str = "http",
    ) -> HecateMCPClient:
        """Connect to an MCP server and register it by name.

        Args:
            name: Unique name for this server connection.
            server_url: URL of the MCP server (for HTTP) or command (for stdio).
            transport: Transport type — ``"http"`` for Streamable HTTP, ``"stdio"`` for subprocess.

        Returns:
            The connected ``HecateMCPClient`` instance.
        """
        if name in self._clients:
            logger.warning("MCP server '%s' already connected, replacing", name)
            await self._clients[name].disconnect()

        client = HecateMCPClient(timeout=self._default_timeout)
        if transport == "http":
            await client.connect_http(server_url)
        elif transport == "stdio":
            await client.connect_stdio(command=server_url, args=[])
        else:
            raise ValueError(f"Unsupported transport: {transport}")

        self._clients[name] = client
        logger.info("MCP server '%s' connected (%s)", name, transport)
        return client

    def get_client(self, name: str) -> HecateMCPClient | None:
        """Get a connected client by name.

        Args:
            name: Server name.

        Returns:
            The client instance, or None if not found.
        """
        return self._clients.get(name)

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Discover tools from all connected servers.

        Returns:
            Aggregated list of tool dicts, each tagged with ``mcp_server`` key.
        """
        all_tools: list[dict[str, Any]] = []
        for name, client in self._clients.items():
            try:
                tools = await client.list_tools()
                for tool in tools:
                    tool["mcp_server"] = name
                all_tools.extend(tools)
            except Exception:
                logger.error("Failed to discover tools from MCP server '%s'", name, exc_info=True)
        return all_tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Call a tool on a specific connected server.

        Args:
            server_name: Name of the server to call.
            tool_name: Name of the tool.
            arguments: Tool arguments.

        Returns:
            The tool execution result.

        Raises:
            ValueError: If the server is not connected.
        """
        client = self._clients.get(server_name)
        if client is None:
            raise ValueError(f"MCP server '{server_name}' is not connected")
        return await client.call_tool(tool_name, arguments)

    async def disconnect_all(self) -> None:
        """Disconnect from all servers and clear the registry."""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
            except Exception:
                logger.error("Error disconnecting MCP server '%s'", name, exc_info=True)
        self._clients.clear()
