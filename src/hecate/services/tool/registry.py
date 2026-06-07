"""Central tool routing service.

Routes tool execution calls by source type (builtin / custom / mcp).
Built-in tools are resolved via in-memory set lookup for fast routing;
non-builtin tools query the ``ToolModel`` database table.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.tool import ToolModel
from hecate.services.tool.builtin import BUILTIN_TOOL_DEFINITIONS, BuiltInToolExecutor

if TYPE_CHECKING:
    from hecate.services.mcp.connection import MCPClientManager

logger = logging.getLogger(__name__)

_ZERO_WORKSPACE = "00000000-0000-0000-0000-000000000000"


class ToolRegistry:
    """Routes tool execution calls by source type.

    Args:
        db: Async database session for non-builtin tool lookups.
        builtin_executor: The built-in tool executor instance.
        mcp_manager: Optional MCP client manager for routing MCP tool calls.
    """

    def __init__(
        self,
        db: AsyncSession,
        builtin_executor: BuiltInToolExecutor,
        mcp_manager: MCPClientManager | None = None,
    ) -> None:
        self._db = db
        self._builtin = builtin_executor
        self._mcp_manager = mcp_manager
        self._builtin_names: set[str] = set(BUILTIN_TOOL_DEFINITIONS.keys())

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a tool by name, routing to the appropriate executor.

        Args:
            name: The registered tool name.
            args: Tool arguments.
            context: Optional execution context (session, node, etc.).

        Returns:
            The tool's return value.

        Raises:
            ValueError: If the tool is not found.
            NotImplementedError: If the tool source routing is not yet implemented.
        """
        # Fast path: builtin tools resolved without DB query
        if name in self._builtin_names:
            return await self._builtin.execute(name, args)

        # DB lookup for non-builtin tools
        result = await self._db.execute(
            select(ToolModel).where(
                ToolModel.name == name,
                ~ToolModel.deleted,
            )
        )
        tool = result.scalar_one_or_none()
        if tool is None:
            raise ValueError(f"Tool '{name}' not found")

        if tool.source == "builtin":
            return await self._builtin.execute(name, args)
        if tool.source == "custom":
            raise NotImplementedError(f"Custom tool execution not yet implemented for '{name}'")
        if tool.source == "mcp":
            if self._mcp_manager is None:
                raise RuntimeError("MCPClientManager not configured in ToolRegistry")
            server_name = tool.mcp_server
            mcp_tool_name = tool.mcp_tool_name or name
            if server_name is None:
                raise ValueError(f"MCP tool '{name}' has no mcp_server configured")
            return await self._mcp_manager.call_tool(server_name, mcp_tool_name, args)
        raise ValueError(f"Unknown tool source: {tool.source!r} for tool '{name}'")


async def seed_builtin_tools(db: AsyncSession) -> int:
    """Seed built-in tool definitions to the database.

    Inserts or updates built-in tools in the ``tools`` table with
    ``source="builtin"`` and ``workspace_id=00000000``. If a tool
    already exists, its description and parameters are updated if changed.

    Args:
        db: Async database session.

    Returns:
        Number of tools inserted or updated.
    """
    count = 0
    import uuid

    zero_ws = uuid.UUID(_ZERO_WORKSPACE)

    for tool_name, tool_def in BUILTIN_TOOL_DEFINITIONS.items():
        result = await db.execute(
            select(ToolModel).where(
                ToolModel.name == tool_name,
                ToolModel.source == "builtin",
                ToolModel.workspace_id == zero_ws,
                ~ToolModel.deleted,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            # Insert new builtin tool
            new_tool = ToolModel(
                workspace_id=zero_ws,
                name=tool_name,
                description=tool_def["description"],
                source="builtin",
                parameters=tool_def["parameters"],
                risk_level="LOW",
                approval_required=False,
                sandbox_enabled=(tool_name == "execute_code"),
            )
            db.add(new_tool)
            count += 1
        else:
            # Update if definition changed
            if existing.description != tool_def["description"] or existing.parameters != tool_def["parameters"]:
                existing.description = tool_def["description"]
                existing.parameters = tool_def["parameters"]
                count += 1

    await db.flush()
    return count
