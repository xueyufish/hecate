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

from hecate.core.config import settings
from hecate.models.tool import ToolModel
from hecate.tools.tool.builtin import (
    BUILTIN_TOOL_DEFINITIONS,
    BuiltInToolExecutor,
    get_memory_tool_names,
)

if TYPE_CHECKING:
    from hecate.tools.gateway.executor import RestToolExecutor
    from hecate.tools.mcp.connection import MCPClientManager
    from hecate.tools.tool.cache import ToolCache

logger = logging.getLogger(__name__)

_ZERO_WORKSPACE = "00000000-0000-0000-0000-000000000000"


class ToolRegistry:
    """Routes tool execution calls by source type.

    Args:
        db: Async database session for non-builtin tool lookups.
        builtin_executor: The built-in tool executor instance.
        mcp_manager: Optional MCP client manager for routing MCP tool calls.
        cache: Optional tool result cache.
        rest_executor: Optional gateway REST executor for ``source="rest"``
            tools; required only when rest tools are executed through this
            registry.
    """

    def __init__(
        self,
        db: AsyncSession,
        builtin_executor: BuiltInToolExecutor,
        mcp_manager: MCPClientManager | None = None,
        cache: ToolCache | None = None,
        rest_executor: RestToolExecutor | None = None,
    ) -> None:
        self._db = db
        self._builtin = builtin_executor
        self._mcp_manager = mcp_manager
        self._cache = cache
        self._rest_executor = rest_executor
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
            return await self._maybe_cache(
                name, args, context, lambda: self._builtin.execute(name, args, context), None
            )

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
            return await self._maybe_cache(
                name, args, context, lambda: self._builtin.execute(name, args, context), tool
            )
        if tool.source == "custom":
            raise NotImplementedError(f"Custom tool execution not yet implemented for '{name}'")
        if tool.source == "mcp":
            if self._mcp_manager is None:
                raise RuntimeError("MCPClientManager not configured in ToolRegistry")
            server_name = tool.mcp_server
            mcp_tool_name = tool.mcp_tool_name or name
            if server_name is None:
                raise ValueError(f"MCP tool '{name}' has no mcp_server configured")
            return await self._maybe_cache(
                name,
                args,
                context,
                lambda: self._mcp_manager.call_tool(server_name, mcp_tool_name, args),
                tool,
            )
        if tool.source == "rest":
            from hecate.models.gateway_target import GatewayTargetModel

            target = await self._db.get(GatewayTargetModel, tool.target_id) if tool.target_id is not None else None
            if target is None or target.deleted or not target.is_active:
                raise ValueError(f"Gateway target unavailable for tool '{name}'")
            if self._rest_executor is None:
                raise RuntimeError("REST executor not configured in ToolRegistry")
            return await self._maybe_cache(
                name,
                args,
                context,
                lambda: self._rest_executor.execute(tool, target, args),
                tool,
            )
        raise ValueError(f"Unknown tool source: {tool.source!r} for tool '{name}'")

    async def _maybe_cache(
        self,
        name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None,
        execute_fn: Any,
        tool: ToolModel | None,
    ) -> Any:
        """Check cache before executing, store result after.

        Args:
            name: Tool name.
            args: Tool arguments.
            context: Optional execution context (for session_id).
            execute_fn: Async callable that executes the tool.
            tool: Optional ToolModel for cache metadata.

        Returns:
            Tool result (cached or fresh).
        """
        if self._cache is None:
            return await execute_fn()

        from hecate.tools.tool.cache import is_cacheable

        tool_meta: dict[str, Any] = {"name": name, "risk_level": "low"}
        if tool is not None:
            tool_meta.update(
                {
                    "risk_level": tool.risk_level,
                    "sandbox_enabled": tool.sandbox_enabled,
                    "cacheable": tool.cacheable,
                }
            )

        if not is_cacheable(tool_meta):
            return await execute_fn()

        session_id = (context or {}).get("session_id") or (context or {}).get("_session_id")
        key = self._cache.make_key(name, args, session_id)

        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("Cache hit for tool '%s'", name)
            return cached

        result = await execute_fn()
        ttl = tool.cache_ttl if tool and tool.cache_ttl else None
        self._cache.set(key, result, ttl, tool_name=name)
        return result


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
    memory_tools = get_memory_tool_names()

    # 4.21 reflection_tools seeding gate — REFLECTION_ENABLED controls
    # whether ``reflection_search`` and ``work_context_query`` enter
    # the platform surface at all. When the flag is off, the seed
    # loop skips these names and the agent cannot mount them.
    from hecate_memory.memory.tools_backend import get_visible_memory_tool_names

    visible_memory_tools = get_visible_memory_tool_names(reflection_enabled=settings.REFLECTION_ENABLED)

    for tool_name, tool_def in BUILTIN_TOOL_DEFINITIONS.items():
        # Memory tools are flag-gated at seeding: flag off → not visible to any
        # agent and the platform surface stays byte-identical.
        if tool_name in memory_tools:
            if not settings.MEMORY_TOOLS_ENABLED:
                continue
            if tool_name == "conversation_search" and not settings.RECALL_INDEXING_ENABLED:
                continue
            # 4.21 reflection_tools: skip when REFLECTION_ENABLED=false.
            if tool_name not in visible_memory_tools:
                continue
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
                risk_level=str(tool_def.get("risk_level", "LOW")).upper(),
                approval_required=False,
                sandbox_enabled=(tool_name == "execute_code"),
            )
            db.add(new_tool)
            count += 1
        else:
            # Update if definition changed
            new_risk = str(tool_def.get("risk_level", "LOW")).upper()
            if (
                existing.description != tool_def["description"]
                or existing.parameters != tool_def["parameters"]
                or existing.risk_level != new_risk
            ):
                existing.description = tool_def["description"]
                existing.parameters = tool_def["parameters"]
                existing.risk_level = new_risk
                count += 1

    await db.flush()
    return count
