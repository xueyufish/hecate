"""Workspace management API endpoints.

Provides CRUD operations for workspaces within organizations.
Workspaces are the resource isolation boundary in the multi-tenant hierarchy.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.workspace import (
    WorkspaceCreateSchema,
    WorkspaceReadSchema,
    WorkspaceUpdateSchema,
)
from hecate.services.organization_service import OrganizationService
from hecate.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/orgs/{org_id}/workspaces", tags=["workspaces"])
_workspace_service = WorkspaceService()
_org_service = OrganizationService()


async def _verify_org_access(
    org_id: str,
    ctx: AuthContext,
    db: AsyncSession,
) -> uuid_mod.UUID:
    """Verify the user has access to the organization."""
    try:
        org_uuid = uuid_mod.UUID(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid org ID", "details": None}},
        ) from None

    org = await _org_service.get(db, org_uuid)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Organization not found", "details": None}},
        )
    if org.owner_id != ctx.user_id and not ctx.is_system_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": "Not the organization owner", "details": None}},
        )
    return org_uuid


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    org_id: str,
    body: WorkspaceCreateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new workspace in an organization."""
    (org_uuid) = await _verify_org_access(org_id, ctx, db)

    try:
        workspace = await _workspace_service.create(
            db,
            org_id=org_uuid,
            name=body.name,
            slug=body.slug,
            creator_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(exc), "details": None}},
        ) from None
    return WorkspaceReadSchema.model_validate(workspace).model_dump()


@router.get("")
async def list_workspaces(
    org_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List workspaces in an organization where the user is a member."""
    (org_uuid) = await _verify_org_access(org_id, ctx, db)
    workspaces, total = await _workspace_service.list_by_org_and_member(db, org_uuid, ctx.user_id, page, page_size)
    return {
        "items": [WorkspaceReadSchema.model_validate(w).model_dump() for w in workspaces],
        "total": total,
    }


@router.get("/{workspace_id}")
async def get_workspace(
    org_id: str,
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a workspace by ID."""
    from uuid import UUID

    _ = await _verify_org_access(org_id, ctx, db)
    try:
        ws_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid workspace ID", "details": None}},
        ) from None

    workspace = await _workspace_service.get(db, ws_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Workspace not found", "details": None}},
        )
    return WorkspaceReadSchema.model_validate(workspace).model_dump()


@router.patch("/{workspace_id}")
async def update_workspace(
    org_id: str,
    workspace_id: str,
    body: WorkspaceUpdateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update a workspace."""
    from uuid import UUID

    _ = await _verify_org_access(org_id, ctx, db)
    try:
        ws_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid workspace ID", "details": None}},
        ) from None

    workspace = await _workspace_service.get(db, ws_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Workspace not found", "details": None}},
        )
    updated = await _workspace_service.update(db, workspace, body)
    await db.refresh(updated)
    return WorkspaceReadSchema.model_validate(updated).model_dump()


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    org_id: str,
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Soft-delete a workspace (must have no active resources)."""
    from uuid import UUID

    _ = await _verify_org_access(org_id, ctx, db)
    try:
        ws_uuid = UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid workspace ID", "details": None}},
        ) from None

    workspace = await _workspace_service.get(db, ws_uuid)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Workspace not found", "details": None}},
        )
    try:
        await _workspace_service.soft_delete(db, workspace)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(exc), "details": None}},
        ) from None
