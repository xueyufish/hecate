"""Federated tool catalog for the MCP Gateway.

Builds the caller-visible federated tool list from gateway targets and
routes federated tool calls. ``rest`` tools come from the projected
``ToolModel`` rows; ``mcp`` tools are live-proxied through the MCP
connection manager (cached ``tools/list`` per server). Federated names are
``<target>__<tool>`` — rest names are stored that way, mcp names are
computed at presentation time.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.gateway_target import GatewayTargetKind, GatewayTargetModel
from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import GatewayError
from hecate.tools.gateway.executor import RestToolExecutor

logger = logging.getLogger(__name__)

_MAX_CATALOG = 500


def federated_tool_name(target_name: str, tool_name: str) -> str:
    """Presentation name for a federated tool."""
    return f"{target_name}__{tool_name}"


def resolve_federated_name(
    exposed_name: str,
    target_names: list[str],
) -> tuple[str, str] | None:
    """Split ``<target>__<tool>`` into its parts by longest-prefix match.

    Args:
        exposed_name: The federated presentation name.
        target_names: Candidate target names.

    Returns:
        ``(target_name, upstream_tool_name)`` or ``None`` when no target
        name is a prefix followed by ``__``.
    """
    for candidate in sorted(target_names, key=len, reverse=True):
        prefix = candidate + "__"
        if exposed_name.startswith(prefix) and len(exposed_name) > len(prefix):
            return candidate, exposed_name[len(prefix) :]
    return None


def _schema_without_extension(schema: dict[str, Any]) -> dict[str, Any]:
    """Projection schema minus the internal ``x-gateway`` metadata."""
    public = {k: v for k, v in (schema or {}).items() if k != "x-gateway"}
    return public


class FederatedCatalog:
    """Builds the federated tool catalog and routes federated calls.

    Args:
        db_session_factory: Async session factory (one session per
            catalog operation — the middleware runs outside request DI).
        mcp_manager: MCP client manager for ``mcp``-kind targets.
        executor: REST executor for ``rest``-kind targets.
    """

    def __init__(
        self,
        db_session_factory: Callable[[], Any],
        mcp_manager: Any = None,
        executor: RestToolExecutor | None = None,
    ) -> None:
        self._session_factory = db_session_factory
        self._mcp_manager = mcp_manager
        self._executor = executor or RestToolExecutor()

    async def _visible_targets(
        self,
        db: AsyncSession,
        workspace_id: str | None,
        include_platform: bool,
    ) -> list[GatewayTargetModel]:
        """Active targets visible to the caller (own workspace, optionally platform)."""
        result = await db.execute(
            select(GatewayTargetModel).where(
                ~GatewayTargetModel.deleted,
                GatewayTargetModel.is_active.is_(True),
            )
        )
        targets = result.scalars().all()
        visible = []
        for target in targets:
            if target.workspace_id is None:
                if include_platform:
                    visible.append(target)
            elif workspace_id is not None and str(target.workspace_id) == workspace_id:
                visible.append(target)
        return visible

    def _rest_tool_dict(self, row: ToolModel) -> dict[str, Any]:
        return {
            "name": row.name,
            "description": row.description,
            "parameters": _schema_without_extension(row.parameters),
            "source": "rest",
            "risk_level": (row.risk_level or "LOW").lower(),
        }

    async def list_federated_tools(
        self,
        workspace_id: str | None,
        include_platform: bool,
    ) -> list[dict[str, Any]]:
        """Caller-visible federated tool entries (rest projections + live mcp lists).

        Upstream ``mcp`` targets that fail discovery are skipped (listing
        stays resilient); rest projections always appear.
        """
        tools: list[dict[str, Any]] = []
        async with self._session_factory() as db:
            targets = await self._visible_targets(db, workspace_id, include_platform)
            rest_target_ids = [t.id for t in targets if t.kind == GatewayTargetKind.REST]
            if rest_target_ids:
                rows = await db.execute(
                    select(ToolModel).where(
                        ToolModel.target_id.in_(rest_target_ids),
                        ToolModel.source == "rest",
                        ~ToolModel.deleted,
                    )
                )
                tools.extend(self._rest_tool_dict(row) for row in rows.scalars().all())
            if self._mcp_manager is not None:
                for target in targets:
                    if target.kind != GatewayTargetKind.MCP:
                        continue
                    try:
                        upstream = await self._mcp_manager.discover_tools(target.name)
                    except Exception as exc:  # noqa: BLE001 — listing must stay resilient
                        logger.warning("Federated listing skipped for target '%s': %s", target.name, exc)
                        continue
                    for entry in upstream:
                        tools.append(
                            {
                                "name": federated_tool_name(target.name, entry.get("name", "")),
                                "description": entry.get("description", ""),
                                "parameters": entry.get("inputSchema") or {"type": "object", "properties": {}},
                                "source": "mcp",
                                "risk_level": "low",
                            }
                        )
        tools.sort(key=lambda t: t["name"])
        return tools[:_MAX_CATALOG]

    async def call_federated_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        workspace_id: str | None,
        include_platform: bool,
    ) -> Any:
        """Execute a federated tool call after resolving it for the caller.

        Raises:
            GatewayError: If the name resolves to no federated tool visible
                to the caller.
        """
        async with self._session_factory() as db:
            targets = await self._visible_targets(db, workspace_id, include_platform)
            row = (
                await db.execute(
                    select(ToolModel).where(
                        ToolModel.name == name,
                        ToolModel.source == "rest",
                        ~ToolModel.deleted,
                    )
                )
            ).scalar_one_or_none()
            if row is not None and row.target_id is not None:
                target = await db.get(GatewayTargetModel, row.target_id)
                if target is not None and target in targets:
                    return await self._executor.execute(row, target, arguments or {})

        if self._mcp_manager is not None:
            mcp_targets = [t for t in targets if t.kind == GatewayTargetKind.MCP]
            resolved = resolve_federated_name(name, [t.name for t in mcp_targets])
            if resolved is not None:
                target_name, upstream_tool = resolved
                return await self._mcp_manager.call_tool(target_name, upstream_tool, arguments or {})

        raise GatewayError(f"Unknown federated tool '{name}'")

    async def is_federated(
        self,
        name: str,
        workspace_id: str | None,
        include_platform: bool,
    ) -> bool:
        """Whether ``name`` resolves to a federated tool visible to the caller."""
        async with self._session_factory() as db:
            target_ref = (
                await db.execute(
                    select(ToolModel.target_id).where(
                        ToolModel.name == name,
                        ToolModel.source == "rest",
                        ~ToolModel.deleted,
                        ToolModel.target_id.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            targets = await self._visible_targets(db, workspace_id, include_platform)
            if target_ref is not None:
                rest_ids = {t.id for t in targets if t.kind == GatewayTargetKind.REST}
                if target_ref in rest_ids:
                    return True
            mcp_names = [t.name for t in targets if t.kind == GatewayTargetKind.MCP]
            return resolve_federated_name(name, mcp_names) is not None


def make_platform_workspace_id() -> uuid.UUID:
    """The zero-UUID used for platform-scope tool rows."""
    return uuid.UUID("00000000-0000-0000-0000-000000000000")
