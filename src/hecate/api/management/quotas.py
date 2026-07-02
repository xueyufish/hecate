"""API router for quota management: definitions CRUD, usage queries, reset."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.quota import (
    QuotaCreateSchema,
    QuotaReadSchema,
    QuotaUpdateSchema,
)
from hecate.services.quota_service import QuotaService

quotas_router = APIRouter(tags=["quotas"])


@quotas_router.post("/quotas", response_model=QuotaReadSchema, status_code=201)
async def create_quota(
    body: QuotaCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> QuotaReadSchema:
    """Create a new quota definition."""
    service = QuotaService(db, workspace_id=ctx.workspace_id)
    quota = await service.create_quota(**body.model_dump())
    await db.commit()
    return QuotaReadSchema.model_validate(quota)


@quotas_router.get("/quotas", response_model=list[QuotaReadSchema])
async def list_quotas(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    resource_type: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> list[QuotaReadSchema]:
    """List quota definitions for the current workspace."""
    service = QuotaService(db, workspace_id=ctx.workspace_id)
    quotas = await service.list_quotas(resource_type=resource_type, scope=scope)
    return [QuotaReadSchema.model_validate(q) for q in quotas]


@quotas_router.get("/quotas/{quota_id}", response_model=QuotaReadSchema)
async def get_quota(
    quota_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> QuotaReadSchema:
    """Get a single quota definition."""
    service = QuotaService(db, workspace_id=ctx.workspace_id)
    quota = await service.get_quota(quota_id)
    if quota is None:
        raise HTTPException(status_code=404, detail="Quota not found")
    return QuotaReadSchema.model_validate(quota)


@quotas_router.put("/quotas/{quota_id}", response_model=QuotaReadSchema)
async def update_quota(
    quota_id: uuid.UUID,
    body: QuotaUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> QuotaReadSchema:
    """Update a quota definition."""
    service = QuotaService(db, workspace_id=ctx.workspace_id)
    quota = await service.update_quota(quota_id, **body.model_dump(exclude_unset=True))
    if quota is None:
        raise HTTPException(status_code=404, detail="Quota not found")
    await db.commit()
    return QuotaReadSchema.model_validate(quota)


@quotas_router.delete("/quotas/{quota_id}", status_code=204)
async def delete_quota(
    quota_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a quota definition."""
    service = QuotaService(db, workspace_id=ctx.workspace_id)
    if not await service.delete_quota(quota_id):
        raise HTTPException(status_code=404, detail="Quota not found")
    await db.commit()


@quotas_router.get("/quotas/usage")
async def list_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    resource_type: Annotated[str | None, Query()] = None,
) -> list[dict]:
    """Get current usage for all quotas in the workspace."""
    service = QuotaService(db, workspace_id=ctx.workspace_id)
    return await service.list_usage(resource_type=resource_type)


@quotas_router.post("/quotas/{quota_id}/reset")
async def reset_quota(
    quota_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Reset the current period's usage to zero. Requires workspace admin role."""
    role = getattr(ctx, "role", None)
    role_str = str(role).lower() if role else ""
    if "admin" not in role_str and scope_is_not_system(ctx):
        raise HTTPException(status_code=403, detail="Workspace admin role required")

    service = QuotaService(db, workspace_id=ctx.workspace_id)
    if not await service.reset_quota(quota_id):
        raise HTTPException(status_code=404, detail="Quota not found")
    await db.commit()
    return {"quota_id": str(quota_id), "status": "reset"}


def scope_is_not_system(ctx: AuthContext) -> bool:
    """Check if the auth context is not system-scoped."""
    return getattr(ctx, "scope", None) != "system"
