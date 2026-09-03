"""MCP (Model Context Protocol) client for tool discovery and execution.

Provides real MCP server connections using the official ``mcp`` Python SDK v2,
supporting Streamable HTTP and stdio transports. Includes protocol-era
negotiation (2026-07-28 preferred, with automatic fallback to the 2025
``initialize`` handshake), health-check support, and per-request timeout.

Public surface (kept stable for downstream callers — 5.4c connection
manager and the egress-filter test suite depend on these signatures):

- ``connect_http(server_url)`` — open a Streamable HTTP session
- ``connect_stdio(command, args, env=None)`` — open a stdio subprocess session
- ``list_tools()`` — list tool descriptors from the connected server
- ``call_tool(tool_name, arguments)`` — invoke a tool; result text is run
  through the egress-filter chain before returning
- ``disconnect()`` — close the underlying session
- ``health_check()`` — cheap liveness probe
- ``connected`` property — whether the session is open
- ``protocol_version`` property — negotiated MCP version, set after connect

The implementation builds on ``mcp.Client`` (the v2 high-level client). The
SDK performs protocol-era negotiation automatically (``mode='auto'`` probes
``server/discover`` and falls back to the ``initialize`` handshake when the
server returns ``-32601``). We eagerly enter the SDK's async context manager
inside ``connect_http`` / ``connect_stdio`` so that the existing
``connected`` semantics of HecateMCPClient are preserved.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from hecate.runtime.security.egress import EgressFilter, EgressResult

logger = logging.getLogger(__name__)


class HecateMCPClient:
    """Production MCP client wrapping the official ``mcp`` SDK v2.

    Args:
        timeout: Connection and request timeout in seconds (mapped to
            ``mcp.Client(..., read_timeout_seconds=timeout)``).
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
        self._client: Client | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected to a server."""
        return self._connected and self._client is not None

    @property
    def protocol_version(self) -> str | None:
        """Negotiated MCP protocol version, set after a successful connection.

        Returns ``None`` if the client is not connected or the SDK has not yet
        recorded a version (e.g. negotiation still in flight on slow servers).
        Forwards ``mcp.Client.protocol_version`` directly.
        """
        if self._client is None:
            return None
        return getattr(self._client, "protocol_version", None)

    async def connect_http(self, server_url: str) -> None:
        """Connect to a remote MCP server via Streamable HTTP.

        Args:
            server_url: Full URL of the MCP endpoint (e.g. ``http://host:port/mcp``).
        """
        logger.info("Connecting to MCP server via HTTP at %s", server_url)
        self._server_url = server_url
        self._client = Client(
            server_url,
            mode="auto",
            read_timeout_seconds=self._timeout,
        )
        # Eagerly enter the SDK's async context manager so that ``connected``
        # is True immediately after ``connect_http`` returns, matching the
        # pre-upgrade semantics relied on by callers and tests.
        await self._client.__aenter__()
        self._connected = True
        logger.info(
            "Connected to MCP server at %s (protocol=%s)",
            server_url,
            self.protocol_version,
        )

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
        logger.info(
            "Connecting to MCP server via stdio: %s %s",
            command,
            " ".join(args),
        )
        server_params = StdioServerParameters(command=command, args=args, env=env)
        self._server_url = f"stdio://{command}"
        self._client = Client(
            server_params,
            mode="auto",
            read_timeout_seconds=self._timeout,
        )
        await self._client.__aenter__()
        self._connected = True
        logger.info(
            "Connected to MCP server via stdio: %s (protocol=%s)",
            command,
            self.protocol_version,
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools from the connected MCP server.

        Returns:
            List of tool dicts with ``name``, ``description``, and ``input_schema`` keys
            (snake_case per MCP 2.0 wire format).

        Raises:
            RuntimeError: If not connected to a server.
        """
        if not self.connected or self._client is None:
            raise RuntimeError("Not connected to an MCP server")

        result = await self._client.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in result.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.input_schema or {},
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
        if not self.connected or self._client is None:
            raise RuntimeError("Not connected to an MCP server")

        logger.info("Calling tool %s", tool_name)
        result = await self._client.call_tool(tool_name, arguments)
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

        from hecate.runtime.security.egress import EgressAction

        out: list[str] = []
        for text in texts:
            current = text
            blocked = False
            for filt in self._egress_filters:
                filt_result: EgressResult = await filt.filter(current, context=context)
                if filt_result.audit_data and self._audit_sink is not None:
                    for record in filt_result.audit_data:
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
                if filt_result.action == EgressAction.BLOCK:
                    blocked = True
                    break
                if filt_result.action == EgressAction.MODIFIED and filt_result.content is not None:
                    current = filt_result.content
            if blocked:
                return None
            out.append(current)
        return out

    async def health_check(self) -> bool:
        """Perform a health check by calling list_tools.

        Returns:
            True if the server responds to list_tools, False otherwise.
        """
        if not self.connected or self._client is None:
            return False
        try:
            await self._client.list_tools()
            return True
        except Exception:
            logger.debug("Health check failed for MCP client", exc_info=True)
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MCP server and clean up resources."""
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                logger.debug("Error during MCP client disconnect", exc_info=True)
            finally:
                self._client = None
        self._connected = False
        logger.info("Disconnected from MCP server")
