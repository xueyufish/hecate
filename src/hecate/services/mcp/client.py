"""MCP (Model Context Protocol) client for tool discovery and execution.

Provides real MCP server connections using the official ``mcp`` Python SDK,
supporting Streamable HTTP and stdio transports. Includes health check
support and per-request timeout.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


class HecateMCPClient:
    """Production MCP client wrapping the official ``mcp`` SDK.

    Supports connecting to MCP servers via Streamable HTTP or stdio transport,
    discovering available tools, executing tool calls, and health checks.

    Args:
        timeout: Connection and request timeout in seconds.
    """

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected to a server."""
        return self._connected and self._session is not None

    async def connect_http(self, server_url: str) -> None:
        """Connect to a remote MCP server via Streamable HTTP.

        Args:
            server_url: Full URL of the MCP endpoint (e.g. ``http://host:port/mcp``).
        """
        logger.info("Connecting to MCP server via HTTP at %s", server_url)
        self._exit_stack = AsyncExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            streamablehttp_client(url=server_url, timeout=self._timeout)
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()
        self._connected = True
        logger.info("Connected to MCP server at %s", server_url)

    async def connect_stdio(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        """Connect to a local MCP server via stdio (subprocess).

        Args:
            command: Executable command (e.g. ``"python"``).
            args: Command arguments (e.g. ``["server.py"]``).
            env: Optional environment variables for the subprocess.
        """
        logger.info("Connecting to MCP server via stdio: %s %s", command, " ".join(args))
        server_params = StdioServerParameters(command=command, args=args, env=env)
        self._exit_stack = AsyncExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(stdio_client(server_params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()
        self._connected = True
        logger.info("Connected to MCP server via stdio: %s", command)

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools from the connected MCP server.

        Returns:
            List of tool dicts with ``name``, ``description``, and ``inputSchema`` keys.

        Raises:
            RuntimeError: If not connected to a server.
        """
        if not self.connected or self._session is None:
            raise RuntimeError("Not connected to an MCP server")

        result = await self._session.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in result.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                }
            )
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute a tool on the connected MCP server.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            The tool execution result content.

        Raises:
            RuntimeError: If not connected to a server.
        """
        if not self.connected or self._session is None:
            raise RuntimeError("Not connected to an MCP server")

        logger.info("Calling tool %s", tool_name)
        result = await self._session.call_tool(tool_name, arguments)
        # Extract text content from result
        if result.content:
            texts = [c.text for c in result.content if hasattr(c, "text")]
            if len(texts) == 1:
                return texts[0]
            return texts
        return None

    async def health_check(self) -> bool:
        """Perform a health check by calling list_tools.

        Returns:
            True if the server responds to list_tools, False otherwise.
        """
        if not self.connected or self._session is None:
            return False
        try:
            await self._session.list_tools()
            return True
        except Exception:
            logger.debug("Health check failed for MCP client", exc_info=True)
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MCP server and clean up resources."""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:
                logger.debug("Error during MCP client disconnect", exc_info=True)
            finally:
                self._exit_stack = None
        self._session = None
        self._connected = False
        logger.info("Disconnected from MCP server")
