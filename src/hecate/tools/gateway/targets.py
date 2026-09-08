"""Gateway target registration and management.

Provides :class:`GatewayTargetService` — CRUD for gateway targets with the
egress baseline (HTTPS enforced on non-loopback hosts) and credential
redaction. ``mcp``-kind targets are additionally registered with the MCP
connection manager so their tools can be federated lazily.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from hecate.tools.mcp.connection import MCPClientManager

from hecate.models.gateway_target import (
    GatewayTargetCreateSchema,
    GatewayTargetModel,
    GatewayTargetUpdateSchema,
)
from hecate.tools.gateway.errors import (
    DuplicateTargetError,
    EgressPolicyError,
    TargetNotFoundError,
)

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def validate_base_url(base_url: str) -> None:
    """Validate a target base URL against the egress baseline.

    HTTPS is required for non-loopback hosts; plain HTTP is allowed on
    loopback only (development convenience). Any scheme other than
    http/https is rejected.

    Args:
        base_url: The URL to validate.

    Raises:
        EgressPolicyError: If the URL violates the baseline.
    """
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise EgressPolicyError(f"Unsupported URL scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise EgressPolicyError("URL has no host")
    is_loopback = host in _LOOPBACK_HOSTS or host.startswith("127.")
    if parsed.scheme == "http" and not is_loopback:
        raise EgressPolicyError("HTTPS is required for non-loopback hosts")


def redact_credentials(credentials: dict) -> dict:
    """Return a copy of ``credentials`` with every leaf value replaced.

    Keys (e.g. header names) are preserved so operators can see the
    credential structure; values never leave the service layer.
    """
    redacted: dict = {}
    for key, value in credentials.items():
        if isinstance(value, dict):
            redacted[key] = redact_credentials(value)
        else:
            redacted[key] = "***"
    return redacted


class GatewayTargetService:
    """CRUD and lifecycle management for gateway targets.

    Args:
        db: Async database session.
        mcp_manager: Optional MCP client manager; when set, ``mcp``-kind
            targets are registered/unregistered with it on create,
            update, and deactivate.
    """

    def __init__(self, db: AsyncSession, mcp_manager: MCPClientManager | None = None) -> None:
        self._db = db
        self._mcp_manager = mcp_manager

    async def create_target(
        self,
        payload: GatewayTargetCreateSchema,
        created_by: uuid.UUID | None = None,
    ) -> GatewayTargetModel:
        """Register a new gateway target.

        Args:
            payload: Target registration payload.
            created_by: Optional user ID of the installer.

        Returns:
            The created model.

        Raises:
            EgressPolicyError: If the base URL violates the egress baseline.
            DuplicateTargetError: If the name is taken in the workspace.
        """
        validate_base_url(payload.base_url)
        existing = await self._find_by_name(payload.name, payload.workspace_id)
        if existing is not None:
            raise DuplicateTargetError(f"Target '{payload.name}' already exists in this workspace")
        target = GatewayTargetModel(
            name=payload.name,
            kind=payload.kind,
            base_url=payload.base_url,
            spec=payload.spec or {},
            credentials=payload.credentials or {},
            workspace_id=payload.workspace_id,
            created_by=created_by,
        )
        self._db.add(target)
        await self._db.flush()
        await self._db.refresh(target)
        self._register_with_mcp_manager(target)
        logger.info(
            "Gateway target '%s' created (kind=%s, workspace=%s)",
            target.name,
            target.kind,
            target.workspace_id,
        )
        return target

    async def get_target(self, target_id: object) -> GatewayTargetModel:
        """Fetch an active target by ID.

        Raises:
            TargetNotFoundError: If no active target with that ID exists.
        """
        key = uuid.UUID(str(target_id)) if not isinstance(target_id, uuid.UUID) else target_id
        target = await self._db.get(GatewayTargetModel, key)
        if target is None or target.deleted or not target.is_active:
            raise TargetNotFoundError(f"Gateway target {target_id} not found")
        return target

    async def list_targets(self, workspace_id: object | None = None) -> list[GatewayTargetModel]:
        """List active targets, optionally scoped to one workspace.

        Args:
            workspace_id: When set, only targets owned by that workspace
                are returned; when ``None``, all active targets.
        """
        query = select(GatewayTargetModel).where(
            ~GatewayTargetModel.deleted,
            GatewayTargetModel.is_active.is_(True),
        )
        if workspace_id is not None:
            query = query.where(GatewayTargetModel.workspace_id == workspace_id)
        result = await self._db.execute(query.order_by(GatewayTargetModel.name))
        return list(result.scalars().all())

    async def update_target(self, target_id: object, payload: GatewayTargetUpdateSchema) -> GatewayTargetModel:
        """Update mutable target fields (base_url, spec, credentials, is_active).

        Re-registers with the MCP connection manager when connectivity-
        relevant fields change on an ``mcp`` target.
        """
        target = await self.get_target(target_id)
        connectivity_changed = False
        if payload.base_url is not None:
            validate_base_url(payload.base_url)
            if payload.base_url != target.base_url:
                target.base_url = payload.base_url
                connectivity_changed = True
        if payload.spec is not None:
            target.spec = payload.spec
        if payload.credentials is not None:
            target.credentials = payload.credentials
            connectivity_changed = True
        if payload.is_active is not None:
            target.is_active = payload.is_active
            connectivity_changed = True
        await self._db.flush()
        await self._db.refresh(target)
        if target.kind == "mcp":
            if target.is_active and connectivity_changed:
                self._register_with_mcp_manager(target, replace=True)
            elif not target.is_active:
                self._unregister_from_mcp_manager(target)
        return target

    async def deactivate_target(self, target_id: object) -> GatewayTargetModel:
        """Deactivate a target (soft delete) and remove its MCP registration."""
        target = await self.get_target(target_id)
        target.deleted = True
        target.is_active = False
        await self._db.flush()
        self._unregister_from_mcp_manager(target)
        logger.info("Gateway target '%s' deactivated", target.name)
        return target

    async def _find_by_name(self, name: str, workspace_id: object | None) -> GatewayTargetModel | None:
        query = select(GatewayTargetModel).where(
            GatewayTargetModel.name == name,
            ~GatewayTargetModel.deleted,
        )
        if workspace_id is None:
            query = query.where(GatewayTargetModel.workspace_id.is_(None))
        else:
            query = query.where(GatewayTargetModel.workspace_id == workspace_id)
        result = await self._db.execute(query)
        return result.scalar_one_or_none()

    def _register_with_mcp_manager(self, target: GatewayTargetModel, replace: bool = False) -> None:
        """Register an ``mcp`` target with the connection manager (lazy connect)."""
        if self._mcp_manager is None or target.kind != "mcp":
            return
        if replace:
            self._mcp_manager.registry.unregister(target.name)
        headers = target.credentials.get("headers") if isinstance(target.credentials, dict) else None
        self._mcp_manager.register_server(
            name=target.name,
            endpoint=target.base_url,
            transport="http",
            workspace_id=str(target.workspace_id) if target.workspace_id else None,
            headers=headers if isinstance(headers, dict) else None,
        )

    def _unregister_from_mcp_manager(self, target: GatewayTargetModel) -> None:
        if self._mcp_manager is None or target.kind != "mcp":
            return
        self._mcp_manager.registry.unregister(target.name)
