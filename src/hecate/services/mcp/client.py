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

from hecate.services.security.egress import EgressFilter, EgressResult

logger = logging.getLogger(__name__)


class HecateMCPClient:
    """Production MCP client wrapping the official ``mcp`` SDK.

    Supports connecting to MCP servers via Streamable HTTP or stdio transport,
    discovering available tools, executing tool calls, and health checks.

    Args:
        timeout: Connection and request timeout in seconds.
        server_url: URL of the MCP server (stored for audit context).
        egress_filters: Optional chain of DLP egress filters applied to
            tool result text before returning. The first filter that
            returns ``BLOCK`` short-circuits the call.
        audit_sink: Optional callable that receives
            ``(entity_type, value, start, end, score, recognizer, action, context)``
            for each DLP finding. Use this to persist audit records to
            ``SecurityFindingModel`` without coupling the client to the DB.
    """

    def __init__(
        self,
        timeout: int = 30,
        server_url: str | None = None,
        egress_filters: list[EgressFilter] | None = None,
        audit_sink: Any = None,
    ) -> None:
        self._timeout = timeout
        self._server_url = server_url
        self._egress_filters: list[EgressFilter] = list(egress_filters or [])
        self._audit_sink = audit_sink
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

        Tool result text is passed through the configured egress filter
        chain (if any). The first filter that returns ``BLOCK`` causes
        the call to return ``None`` and prevents the content from
        reaching the LLM context. MASK filters rewrite the text in
        place. AUDIT filters let the text through but record findings
        via the audit sink.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments.

        Returns:
            The (possibly masked) text content, a list of texts, or
            ``None`` if any filter blocked.

        Raises:
            RuntimeError: If not connected to a server.
        """
        if not self.connected or self._session is None:
            raise RuntimeError("Not connected to an MCP server")

        logger.info("Calling tool %s", tool_name)
        result = await self._session.call_tool(tool_name, arguments)
        if not result.content:
            return None

        texts = [c.text for c in result.content if hasattr(c, "text")]
        processed = await self._apply_egress_filters(texts, context={"tool": tool_name, "server": self._server_url})
        if processed is None:
            return None
        if len(processed) == 1:
            return processed[0]
        return processed

    async def _apply_egress_filters(
        self,
        texts: list[str],
        context: dict[str, Any],
    ) -> list[str] | None:
        """Run every configured egress filter over each text chunk.

        Returns the (possibly masked) list, or ``None`` if any filter
        blocked. Emits audit records for any DLP findings via the
        configured audit sink.
        """
        if not self._egress_filters:
            return list(texts)

        from hecate.services.security.egress import EgressAction

        out: list[str] = []
        for text in texts:
            current = text
            blocked = False
            for filt in self._egress_filters:
                result: EgressResult = await filt.filter(current, context=context)
                if result.audit_data and self._audit_sink is not None:
                    for record in result.audit_data:
                        self._audit_sink(
                            entity_type=record.get("entity_type", "UNKNOWN"),
                            value=record.get("value", ""),
                            start=record.get("start", 0),
                            end=record.get("end", 0),
                            score=record.get("score", 0.0),
                            recognizer=record.get("recognizer", "unknown"),
                            action=record.get("action", "allow"),
                            context={**context, "filter": filt.__class__.__name__},
                        )
                if result.action == EgressAction.BLOCK:
                    blocked = True
                    break
                if result.action == EgressAction.MODIFIED and result.content is not None:
                    current = result.content
            if blocked:
                return None
            out.append(current)
        return out

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
