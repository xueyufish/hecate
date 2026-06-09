"""API key management endpoints.

Provides CRUD operations for database-backed API keys with
system and workspace scoping.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.api_key import (
    ApiKeyCreateResponseSchema,
    ApiKeyCreateSchema,
    ApiKeyReadSchema,
)
from hecate.services.api_key_service import ApiKeyService

router = APIRouter(prefix="/api-keys", tags=["api-keys"])
_api_key_service = ApiKeyService()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new API key."""
    if body.scope.value == "workspace":
        if body.workspace_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": "workspace_id is required for workspace-scoped keys",
                        "details": None,
                    }
                },
            )
        org_id = ctx.org_id
    else:
        org_id = None

    try:
        api_key, raw_key = await _api_key_service.create_key(
            db,
            name=body.name,
            scope=body.scope,
            created_by=ctx.user_id,
            workspace_id=body.workspace_id,
            org_id=org_id,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": str(exc), "details": None}},
        ) from None

    return ApiKeyCreateResponseSchema(
        id=api_key.id,
        name=api_key.name,
        key=raw_key,
        key_prefix=api_key.key_prefix,
        scope=api_key.scope,
        org_id=api_key.org_id,
        workspace_id=api_key.workspace_id,
        created_at=api_key.created_at,
    ).model_dump()


@router.get("")
async def list_api_keys(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List API keys created by the authenticated user."""
    keys, total = await _api_key_service.list_keys(db, ctx.user_id, page, page_size)
    return {
        "items": [ApiKeyReadSchema.model_validate(k).model_dump() for k in keys],
        "total": total,
    }


@router.get("/{key_id}")
async def get_api_key(
    key_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get API key details (excludes the full key)."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid key ID", "details": None}},
        ) from None

    keys, _ = await _api_key_service.list_keys(db, ctx.user_id, page_size=1000)
    key = next((k for k in keys if k.id == key_uuid), None)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "API key not found", "details": None}},
        )
    return ApiKeyReadSchema.model_validate(key).model_dump()


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Revoke an API key."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid key ID", "details": None}},
        ) from None

    try:
        await _api_key_service.revoke_key(db, key_uuid)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "API key not found", "details": None}},
        ) from None


@router.post("/{key_id}/rotate")
async def rotate_api_key(
    key_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Rotate an API key — creates replacement, revokes old."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "BAD_REQUEST", "message": "Invalid key ID", "details": None}},
        ) from None

    try:
        new_key, raw_key = await _api_key_service.rotate_key(db, key_uuid)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(exc), "details": None}},
        ) from None

    return ApiKeyCreateResponseSchema(
        id=new_key.id,
        name=new_key.name,
        key=raw_key,
        key_prefix=new_key.key_prefix,
        scope=new_key.scope,
        org_id=new_key.org_id,
        workspace_id=new_key.workspace_id,
        created_at=new_key.created_at,
    ).model_dump()
