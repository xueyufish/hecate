"""API router for cost aggregation queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.services.cost_service import CostService

router = APIRouter()


@router.get("/costs/summary")
async def get_cost_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    start_date: Annotated[datetime, Query()],
    end_date: Annotated[datetime, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
) -> dict:
    """Get cost summary for a time range with optional filters."""
    service = CostService(db)
    result = await service.get_cost_summary(
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        model=model,
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
    )
    return result.model_dump(mode="json")


@router.get("/costs/breakdown")
async def get_cost_breakdown(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    group_by: Annotated[str, Query(pattern="^(model|agent|user|session)$")],
    start_date: Annotated[datetime, Query()],
    end_date: Annotated[datetime, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
) -> dict:
    """Get cost breakdown by a specified dimension."""
    service = CostService(db)
    entries = await service.get_cost_breakdown(
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        model=model,
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
    )
    return {
        "group_by": group_by,
        "entries": [e.model_dump(mode="json") for e in entries],
    }


@router.get("/costs/timeseries")
async def get_cost_timeseries(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    granularity: Annotated[str, Query(pattern="^(hourly|daily|monthly)$")],
    start_date: Annotated[datetime, Query()],
    end_date: Annotated[datetime, Query()],
    user_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
) -> dict:
    """Get cost timeseries with specified granularity."""
    service = CostService(db)
    points = await service.get_cost_timeseries(
        granularity=granularity,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        model=model,
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
    )
    return {
        "granularity": granularity,
        "points": [p.model_dump(mode="json") for p in points],
    }
