"""REST management API for MCP Gateway targets.

CRUD for gateway targets plus OpenAPI tool projection. All read paths
return redacted credentials. Every endpoint returns 404-class errors while
``GATEWAY_ENABLED`` is false.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.models.gateway_target import (
    GatewayTargetCreateSchema,
    GatewayTargetReadSchema,
    GatewayTargetUpdateSchema,
)
from hecate.tools.api.mcp import get_mcp_manager
from hecate.tools.gateway.converter import project_target_tools
from hecate.tools.gateway.errors import GatewayError
from hecate.tools.gateway.targets import (
    GatewayTargetService,
    redact_credentials,
)

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


def _require_enabled() -> None:
    if not settings.GATEWAY_ENABLED:
        raise HTTPException(status_code=404, detail="MCP Gateway is disabled")


def _to_read(target) -> GatewayTargetReadSchema:
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


@router.get("/targets")
async def list_targets(db: Annotated[AsyncSession, Depends(get_db)]) -> list[GatewayTargetReadSchema]:
    """List active gateway targets (credentials redacted)."""
    _require_enabled()
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    targets = await service.list_targets()
    return [_to_read(t) for t in targets]


@router.post("/targets", status_code=201)
async def create_target(payload: GatewayTargetCreateSchema, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    """Register a target; ``rest`` targets also project their OpenAPI tools."""
    _require_enabled()
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    try:
        target = await service.create_target(payload)
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


@router.get("/targets/{target_id}")
async def get_target(target_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> GatewayTargetReadSchema:
    """Fetch one target (credentials redacted)."""
    _require_enabled()
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    try:
        target = await service.get_target(target_id)
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_read(target)


@router.patch("/targets/{target_id}")
async def update_target(
    target_id: uuid.UUID, payload: GatewayTargetUpdateSchema, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict:
    """Update a target; re-projects tools when a rest spec changes."""
    _require_enabled()
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    try:
        target = await service.update_target(target_id, payload)
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    import_report: dict = {}
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


@router.delete("/targets/{target_id}")
async def deactivate_target(target_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    """Deactivate a target (soft delete) and remove its MCP registration."""
    _require_enabled()
    service = GatewayTargetService(db, mcp_manager=get_mcp_manager())
    try:
        target = await service.deactivate_target(target_id)
    except GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return {"deactivated": True, "id": str(target.id)}
