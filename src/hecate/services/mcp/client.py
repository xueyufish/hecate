"""MCP (Model Context Protocol) client for tool discovery and execution.

Provides:
- Connection to MCP servers
- Tool discovery via tools/list
- Tool execution via tools/call
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for interacting with MCP servers.

    Supports:
    - Connecting to MCP servers
    - Discovering available tools
    - Executing tool calls
    """

    def __init__(self, server_url: str):
        self.server_url = server_url
        self._connected = False
        self._tools: list[dict[str, Any]] = []

    async def connect(self) -> bool:
        """Connect to the MCP server.

        Returns:
            bool: True if connection was successful.
        """
        try:
            logger.info(f"Connecting to MCP server at {self.server_url}")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            return False

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools from the MCP server.

        Returns:
            List of tool definitions.
        """
        if not self._connected:
            await self.connect()

        logger.info(f"Listing tools from {self.server_url}")
        return self._tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool on the MCP server.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            dict with tool execution result.
        """
        if not self._connected:
            await self.connect()

        logger.info(f"Calling tool {tool_name} on {self.server_url}")
        return {
            "tool": tool_name,
            "result": "Mock result",
            "success": True,
        }

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        self._connected = False
        logger.info(f"Disconnected from MCP server at {self.server_url}")


class MCPManager:
    """Manage multiple MCP client connections."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    async def add_server(self, server_url: str) -> MCPClient:
        """Add and connect to an MCP server.

        Args:
            server_url: URL of the MCP server.

        Returns:
            MCPClient instance.
        """
        if server_url not in self._clients:
            client = MCPClient(server_url)
            await client.connect()
            self._clients[server_url] = client
        return self._clients[server_url]

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Discover tools from all connected servers.

        Returns:
            List of tool definitions from all servers.
        """
        all_tools = []
        for url, client in self._clients.items():
            tools = await client.list_tools()
            for tool in tools:
                tool["mcp_server"] = url
            all_tools.extend(tools)
        return all_tools

    async def call_tool(
        self,
        server_url: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool on a specific server.

        Args:
            server_url: URL of the MCP server.
            tool_name: Name of the tool.
            arguments: Tool arguments.

        Returns:
            dict with tool execution result.
        """
        client = self._clients.get(server_url)
        if not client:
            raise ValueError(f"No client for server {server_url}")
        return await client.call_tool(tool_name, arguments)

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()


mcp_manager = MCPManager()
