"""MCP Tool synchronization service.

Synchronizes tools discovered from MCP servers into the Hecate
tool registry for use by agents.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.mcp.client import mcp_manager

logger = logging.getLogger(__name__)


class MCPToolSync:
    """Synchronize MCP tools with Hecate tool registry.

    Provides:
    - Tool discovery from MCP servers
    - Conversion to Hecate tool format
    - Sync with database
    """

    async def sync_tools(
        self,
        server_url: str,
    ) -> list[dict[str, Any]]:
        """Sync tools from an MCP server.

        Args:
            server_url: URL of the MCP server.

        Returns:
            List of synced tool definitions.
        """
        client = await mcp_manager.add_server(server_url)
        mcp_tools = await client.list_tools()

        hecate_tools = []
        for tool in mcp_tools:
            hecate_tool = self._convert_tool(tool, server_url)
            hecate_tools.append(hecate_tool)

        logger.info(f"Synced {len(hecate_tools)} tools from {server_url}")
        return hecate_tools

    def _convert_tool(
        self,
        mcp_tool: dict[str, Any],
        server_url: str,
    ) -> dict[str, Any]:
        """Convert MCP tool to Hecate format.

        Args:
            mcp_tool: MCP tool definition.
            server_url: Source MCP server URL.

        Returns:
            dict in Hecate tool format.
        """
        return {
            "name": mcp_tool.get("name", "unknown"),
            "description": mcp_tool.get("description", ""),
            "source": "mcp",
            "parameters": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
            "returns": mcp_tool.get("outputSchema"),
            "risk_level": "LOW",
            "approval_required": False,
            "mcp_server": server_url,
            "mcp_tool_name": mcp_tool.get("name", "unknown"),
        }


mcp_tool_sync = MCPToolSync()
