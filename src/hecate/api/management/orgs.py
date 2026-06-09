"""Organization management API endpoints.

Provides CRUD operations for organizations — the top-level entity
in the multi-tenant hierarchy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.organization import (
    OrganizationCreateSchema,
    OrganizationReadSchema,
    OrganizationUpdateSchema,
)
from hecate.services.organization_service import OrganizationService

router = APIRouter(prefix="/orgs", tags=["organizations"])
_org_service = OrganizationService()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrganizationCreateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new organization."""
    try:
        org = await _org_service.create(
            db,
            name=body.name,
            slug=body.slug,
            owner_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(exc), "details": None}},
        ) from None
    return OrganizationReadSchema.model_validate(org).model_dump()


@router.get("")
async def list_orgs(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List organizations owned by the authenticated user."""
    orgs, total = await _org_service.list_by_owner(db, ctx.user_id, page, page_size)
    return {
        "items": [OrganizationReadSchema.model_validate(o).model_dump() for o in orgs],
        "total": total,
    }


@router.get("/{org_id}")
async def get_org(
    org_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get an organization by ID."""
    from uuid import UUID

    try:
        org_uuid = UUID(org_id)
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
    return OrganizationReadSchema.model_validate(org).model_dump()


@router.patch("/{org_id}")
async def update_org(
    org_id: str,
    body: OrganizationUpdateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update an organization."""
    from uuid import UUID

    try:
        org_uuid = UUID(org_id)
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

    updated = await _org_service.update(db, org, body)
    await db.refresh(updated)
    return OrganizationReadSchema.model_validate(updated).model_dump()


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Soft-delete an organization."""
    from uuid import UUID

    try:
        org_uuid = UUID(org_id)
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

    await _org_service.soft_delete(db, org)


@router.post("/{org_id}/transfer-ownership")
async def transfer_ownership(
    org_id: str,
    body: dict[str, str],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Transfer organization ownership to another user."""
    from uuid import UUID

    try:
        org_uuid = UUID(org_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid org ID", "details": None}},
        ) from None

    new_owner_id_raw = body.get("new_owner_id")
    if not new_owner_id_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "new_owner_id is required", "details": None}},
        )
    try:
        new_owner_id = UUID(new_owner_id_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid new_owner_id", "details": None}},
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

    try:
        updated = await _org_service.transfer_ownership(db, org, new_owner_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": str(exc), "details": None}},
        ) from None
    return OrganizationReadSchema.model_validate(updated).model_dump()
