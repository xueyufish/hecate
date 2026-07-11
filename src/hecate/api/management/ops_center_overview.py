"""Ops Center overview REST API — unified aggregation endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.services.ops_center.overview import OpsCenterOverviewService

router = APIRouter(prefix="/api/ops-center", tags=["ops-center"])


@router.get("/overview")
async def get_overview(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Unified overview from all Ops Center subsystems."""
    service = OpsCenterOverviewService(db)
    return await service.get_overview(start_date, end_date)


@router.get("/recent-activity")
async def get_recent_activity(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    limit: int = Query(20, ge=1, le=100),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Recent notable events across all subsystems."""
    service = OpsCenterOverviewService(db)
    return await service.get_recent_activity(start_date, end_date, limit)
