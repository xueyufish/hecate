"""Agent health REST API — per-agent health metrics and fleet overview."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.services.ops_center.agent_health import AgentHealthService

router = APIRouter(prefix="/api/ops-center/agents", tags=["ops-center"])


@router.get("/overview")
async def get_fleet_overview(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Aggregate fleet-wide health metrics."""
    service = AgentHealthService(db)
    return await service.get_fleet_overview(start_date, end_date)


@router.get("/{agent_id}/health")
async def get_agent_health(
    agent_id: uuid.UUID,
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Health metrics for a specific agent."""
    service = AgentHealthService(db)
    result = await service.get_agent_health(agent_id, start_date, end_date)
    if result is None:
        raise HTTPException(status_code=404, detail="Agent not found or no activity")
    return result


@router.get("/{agent_id}/trends")
async def get_agent_trends(
    agent_id: uuid.UUID,
    days: int = Query(7, ge=1, le=90),  # noqa: B008
    granularity: str = Query("daily", pattern="^(hourly|daily|weekly)$"),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Time-series health trends for an agent."""
    service = AgentHealthService(db)
    return await service.get_agent_trends(agent_id, days, granularity)
