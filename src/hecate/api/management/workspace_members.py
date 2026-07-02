"""Workspace member management API endpoints.

Provides endpoints for adding, removing, and updating member roles
within workspaces, with enforcement of the last-admin invariant.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.workspace_member import (
    WorkspaceMemberCreateSchema,
    WorkspaceMemberReadSchema,
    WorkspaceMemberUpdateSchema,
)
from hecate.services.workspace_member_service import WorkspaceMemberService

router = APIRouter(
    prefix="/orgs/{org_id}/workspaces/{workspace_id}/members",
    tags=["workspace-members"],
)
_member_service = WorkspaceMemberService()


def _parse_uuid(value: str, field_name: str) -> uuid_mod.UUID:
    """Parse a UUID string or raise 400."""
    try:
        return uuid_mod.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": f"Invalid {field_name}", "details": None}},
        ) from None


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_member(
    org_id: str,
    workspace_id: str,
    body: WorkspaceMemberCreateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Add a member to a workspace."""
    ws_uuid = _parse_uuid(workspace_id, "workspace_id")

    try:
        membership = await _member_service.add_member(
            db,
            workspace_id=ws_uuid,
            user_id=body.user_id,
            role=body.role,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(exc), "details": None}},
        ) from None
    return WorkspaceMemberReadSchema.model_validate(membership).model_dump()


@router.get("")
async def list_members(
    org_id: str,
    workspace_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List members of a workspace."""
    ws_uuid = _parse_uuid(workspace_id, "workspace_id")
    members, total = await _member_service.list_members(db, ws_uuid, page, page_size)
    return {
        "items": [WorkspaceMemberReadSchema.model_validate(m).model_dump() for m in members],
        "total": total,
    }


@router.patch("/{user_id}")
async def update_member_role(
    org_id: str,
    workspace_id: str,
    user_id: str,
    body: WorkspaceMemberUpdateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update a member's role in a workspace."""
    ws_uuid = _parse_uuid(workspace_id, "workspace_id")
    target_user = _parse_uuid(user_id, "user_id")

    try:
        updated = await _member_service.update_role(db, ws_uuid, target_user, body.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(exc), "details": None}},
        ) from None
    return WorkspaceMemberReadSchema.model_validate(updated).model_dump()


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: str,
    workspace_id: str,
    user_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Remove a member from a workspace."""
    ws_uuid = _parse_uuid(workspace_id, "workspace_id")
    target_user = _parse_uuid(user_id, "user_id")

    try:
        await _member_service.remove_member(db, ws_uuid, target_user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(exc), "details": None}},
        ) from None
