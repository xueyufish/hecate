"""Model Catalog REST API — browse, search, compare models."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.model_hub.catalog_service import CatalogService

router = APIRouter(prefix="/api/models/catalog", tags=["model-catalog"])


@router.get("")
async def list_models(
    provider: str | None = Query(None),
    capability: str | None = Query(None),
    model_type: str | None = Query(None),
    min_context: int | None = Query(None, ge=0),
    max_input_price: float | None = Query(None, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """List model catalog with filters and pagination."""
    service = CatalogService(db)
    return await service.list_models(
        provider=provider,
        capability=capability,
        model_type=model_type,
        min_context=min_context,
        max_input_price=max_input_price,
        page=page,
        page_size=page_size,
    )


@router.get("/compare")
async def compare_models(
    model_ids: str = Query(..., description="Comma-separated model IDs"),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Compare multiple models side-by-side."""
    ids = [mid.strip() for mid in model_ids.split(",") if mid.strip()]
    service = CatalogService(db)
    return await service.compare_models(ids)


@router.get("/{model_id}")
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Get detailed model catalog entry."""
    service = CatalogService(db)
    entry = await service.get_model(model_id)
    if entry is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    return entry
