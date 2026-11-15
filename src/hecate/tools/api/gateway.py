"""REST management API for MCP Gateway targets.

CRUD for gateway targets plus OpenAPI tool projection. All read paths
return redacted credentials. Every endpoint returns 404-class errors while
``GATEWAY_ENABLED`` is false — the enabled gate runs before authentication,
so a disabled gateway is indistinguishable from a missing route.

Management authorization (see ``docs/how-to/secure-gateway-rest-targets.md``):

- Reads use the caller's workspace membership. Platform administrators
  can also see global targets, and their bootstrap tokens can see all targets.
- Writes to workspace-owned ``rest`` targets require the workspace ADMIN
  role; the owning workspace always comes from the authenticated identity —
  a request body cannot assign another tenant.
- Global targets (``workspace_id`` NULL) and ``mcp``-kind targets mutate
  platform-wide state (the shared MCP registry is name-indexed across
  workspaces), so their management is gated on the platform-admin identity.
  Workspace-scoped MCP management is deferred until the registry indexes
  registrations by workspace.
- Foreign and missing targets are one indistinguishable 404; a rejected
  request writes no target, tool or registry state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    ensure_workspace_role,
    get_auth_context,
    is_platform_admin,
    security_scheme,
)
from hecate.models.gateway_target import (
    GatewayTargetCreateSchema,
    GatewayTargetKind,
    GatewayTargetModel,
    GatewayTargetReadSchema,
    GatewayTargetUpdateSchema,
)
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel
from hecate.tools.api.mcp import get_mcp_manager
from hecate.tools.gateway.converter import project_target_tools
from hecate.tools.gateway.errors import GatewayError
from hecate.tools.gateway.targets import (
    GatewayTargetService,
    redact_credentials,
)

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


def _require_enabled() -> None:
    """404-class gate: a disabled gateway exposes no management surface.

    Declared as a route-level dependency so it runs before authentication:
    a disabled gateway answers 404 to every caller, anonymous included.
    """
    if not settings.GATEWAY_ENABLED:
        raise HTTPException(status_code=404, detail="MCP Gateway is disabled")


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": message, "details": None}})


def _require_workspace_role(ctx: AuthContext, minimum: WorkspaceRole) -> None:
    try:
        ensure_workspace_role(ctx, minimum)
    except WorkspaceRoleRequiredError as exc:
        raise _forbidden(exc.message) from exc


@dataclass(frozen=True)
class _TargetAccess:
    ctx: AuthContext
    platform_admin: bool

    def require_write(self, workspace_id: uuid.UUID | None, kind: GatewayTargetKind) -> None:
        """Authorize workspace REST writes and platform-wide mutations."""
        if self.ctx.is_system_scope or workspace_id is None or kind == GatewayTargetKind.MCP:
            if not self.platform_admin:
                raise _forbidden("Platform admin required")
            return
        _require_workspace_role(self.ctx, WorkspaceRole.ADMIN)


async def _target_access(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> _TargetAccess:
    platform_admin = await is_platform_admin(credentials, ctx, db)
    if ctx.is_system_scope:
        if not platform_admin:
            raise _forbidden("Platform admin required")
        return _TargetAccess(ctx, platform_admin)
    if ctx.api_key_scope == "workspace" and ctx.workspace_id is not None:
        # The API-key provider supplies a fixed runtime role. Management
        # must use the owner's current role in the key's actual workspace.
        role = await db.scalar(
            select(WorkspaceMemberModel.role)
            .join(WorkspaceModel, WorkspaceModel.id == WorkspaceMemberModel.workspace_id)
            .where(
                WorkspaceMemberModel.user_id == ctx.user_id,
                WorkspaceMemberModel.workspace_id == ctx.workspace_id,
                WorkspaceMemberModel.deleted.is_(False),
                WorkspaceModel.deleted.is_(False),
            )
        )
        ctx = replace(ctx, role=role)
    if ctx.workspace_id is not None or not platform_admin:
        _require_workspace_role(ctx, WorkspaceRole.VIEWER)
    return _TargetAccess(ctx, platform_admin)


TargetAccessDep = Annotated[_TargetAccess, Depends(_target_access)]


def _to_read(target: GatewayTargetModel) -> GatewayTargetReadSchema:
    return GatewayTargetReadSchema(
        id=target.id,
        name=target.name,
        kind=target.kind,
        base_url=target.base_url,
        credentials=redact_credentials(target.credentials or {}),
        workspace_id=target.workspace_id,
        is_active=target.is_active,
        created_at=target.created_at,
    )


@router.get("/targets", dependencies=[Depends(_require_enabled)])
async def list_targets(
    access: TargetAccessDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[GatewayTargetReadSchema]:
    """List active gateway targets visible to the caller (credentials redacted)."""
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    if access.ctx.is_system_scope:
        targets = await service.list_targets()
    else:
        targets = await service.list_targets(access.ctx.workspace_id, include_global=access.platform_admin)
    return [_to_read(t) for t in targets]


@router.post("/targets", status_code=201, dependencies=[Depends(_require_enabled)])
async def create_target(
    payload: GatewayTargetCreateSchema,
    access: TargetAccessDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Register a target; ``rest`` targets also project their OpenAPI tools.

    A scoped request defaults to its workspace. An explicit null selects
    the platform-managed global scope.
    """
    ctx = access.ctx
    workspace_id = payload.workspace_id if "workspace_id" in payload.model_fields_set else ctx.workspace_id
    if workspace_id is not None and not ctx.is_system_scope and workspace_id != ctx.workspace_id:
        raise _forbidden("Targets can only be registered in the caller's workspace")
    access.require_write(workspace_id, payload.kind)

    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    try:
        target = await service.create_target(
            payload.model_copy(update={"workspace_id": workspace_id}), created_by=ctx.user_id
        )
    except GatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    import_report: dict = {}
    if payload.kind == "rest" and payload.spec:
        try:
            report = await project_target_tools(db, target)
            import_report = {
                "imported": report.imported,
                "skipped": report.skipped,
                "warnings": report.warnings,
            }
        except GatewayError as exc:
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return {"target": _to_read(target).model_dump(mode="json"), "import": import_report}


async def _fetch_visible_target(
    target_id: uuid.UUID,
    access: _TargetAccess,
    service: GatewayTargetService,
) -> GatewayTargetModel:
    try:
        if access.ctx.is_system_scope:
            return await service.get_target(target_id)
        return await service.get_target_scoped(target_id, access.ctx.workspace_id, include_global=access.platform_admin)
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/targets/{target_id}", dependencies=[Depends(_require_enabled)])
async def get_target(
    target_id: uuid.UUID,
    access: TargetAccessDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GatewayTargetReadSchema:
    """Fetch one visible target (credentials redacted)."""
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    target = await _fetch_visible_target(target_id, access, service)
    return _to_read(target)


@router.patch("/targets/{target_id}", dependencies=[Depends(_require_enabled)])
async def update_target(
    target_id: uuid.UUID,
    payload: GatewayTargetUpdateSchema,
    access: TargetAccessDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update a visible target; re-projects tools when a rest spec changes."""
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    target = await _fetch_visible_target(target_id, access, service)
    access.require_write(target.workspace_id, target.kind)

    import_report: dict = {}
    try:
        target = await service.update_target(target_id, payload)
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if target.kind == "rest" and payload.spec is not None:
        try:
            report = await project_target_tools(db, target)
            import_report = {
                "imported": report.imported,
                "skipped": report.skipped,
                "warnings": report.warnings,
            }
        except GatewayError as exc:
            await db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return {"target": _to_read(target).model_dump(mode="json"), "import": import_report}


@router.delete("/targets/{target_id}", dependencies=[Depends(_require_enabled)])
async def deactivate_target(
    target_id: uuid.UUID,
    access: TargetAccessDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Deactivate a visible target (soft delete) and remove its MCP registration."""
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    target = await _fetch_visible_target(target_id, access, service)
    access.require_write(target.workspace_id, target.kind)

    try:
        target = await service.deactivate_target(target_id)
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return {"deactivated": True, "id": str(target.id)}
