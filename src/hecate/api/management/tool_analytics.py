"""Tool analytics REST API — per-tool execution metrics."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.services.ops_center.tool_analytics import ToolAnalyticsService

router = APIRouter(prefix="/api/ops-center/tools", tags=["ops-center"])


@router.get("/overview")
async def get_overview(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    agent_id: uuid.UUID | None = Query(None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Aggregate tool execution metrics for a time range."""
    service = ToolAnalyticsService(db)
    return await service.get_overview(start_date, end_date, agent_id=agent_id)


@router.get("/trends")
async def get_trends(
    granularity: str = Query("daily", pattern="^(hourly|daily|weekly)$"),  # noqa: B008
    days: int = Query(7, ge=1, le=90),  # noqa: B008
    tool_name: str | None = Query(None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Time-series data for tool executions."""
    service = ToolAnalyticsService(db)
    return await service.get_trends(granularity, days, tool_name=tool_name)


@router.get("/errors")
async def get_top_errors(
    limit: int = Query(20, ge=1, le=100),  # noqa: B008
    tool_name: str | None = Query(None),  # noqa: B008
    start_date: datetime | None = Query(None),  # noqa: B008
    end_date: datetime | None = Query(None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Most frequent tool execution errors."""
    service = ToolAnalyticsService(db)
    return await service.get_top_errors(limit, tool_name=tool_name, start_date=start_date, end_date=end_date)


@router.get("/{tool_name}")
async def get_tool_details(
    tool_name: str,
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Detailed metrics for a specific tool."""
    service = ToolAnalyticsService(db)
    result = await service.get_tool_details(tool_name, start_date, end_date)
    if result is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return result
