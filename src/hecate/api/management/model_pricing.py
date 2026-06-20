"""API router for model pricing CRUD management."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.model_pricing import (
    ModelPricingCreateSchema,
    ModelPricingUpdateSchema,
)
from hecate.services.cost_service import CostService

router = APIRouter()


@router.post("/model-pricing", status_code=status.HTTP_201_CREATED)
async def create_pricing(
    data: ModelPricingCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new model pricing entry."""
    service = CostService(db)
    result = await service.create_pricing(
        data,
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
    )
    return result.model_dump(mode="json")


@router.get("/model-pricing")
async def list_pricing(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    model_id: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List model pricing entries with optional filter and pagination."""
    service = CostService(db)
    result = await service.list_pricing(
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
        model_id=model_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [item.model_dump(mode="json") for item in result["items"]],
        "total": result["total"],
    }


@router.put("/model-pricing/{pricing_id}")
async def update_pricing(
    pricing_id: uuid.UUID,
    data: ModelPricingUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update a model pricing entry."""
    service = CostService(db)
    try:
        result = await service.update_pricing(pricing_id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e
    return result.model_dump(mode="json")


@router.delete("/model-pricing/{pricing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pricing(
    pricing_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft delete a model pricing entry."""
    service = CostService(db)
    try:
        await service.delete_pricing(pricing_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e
