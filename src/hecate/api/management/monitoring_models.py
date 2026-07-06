"""Model monitoring REST API — performance, drift, trends."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.model_hub.monitoring import MonitoringService

router = APIRouter(prefix="/api/monitoring/models", tags=["monitoring"])


@router.get("/{model_id}/performance")
async def get_model_performance(
    model_id: str,
    start_date: datetime | None = Query(None),  # noqa: B008
    end_date: datetime | None = Query(None),  # noqa: B008
    granularity: str = Query("daily", pattern="^(hourly|daily|weekly)$"),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = MonitoringService(db)
    return await service.get_model_performance(model_id, start_date, end_date, granularity)


@router.get("/compare")
async def compare_models(
    model_ids: str = Query(..., description="Comma-separated model IDs"),
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    ids = [mid.strip() for mid in model_ids.split(",") if mid.strip()]
    service = MonitoringService(db)
    return await service.compare_models(ids, days)


@router.get("/{model_id}/drift")
async def get_model_drift(
    model_id: str,
    metric: str = Query("avg_latency", pattern="^(avg_latency|ttft|error_rate)$"),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = MonitoringService(db)
    return await service.detect_drift(model_id, metric)


@router.get("/trends")
async def get_cost_trends(
    workspace_id: uuid.UUID | None = None,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    service = MonitoringService(db)
    return await service.get_cost_trends_by_model(workspace_id, days)
