"""Model cost budget management REST API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.model_hub.cost_management import CostBudgetService
from hecate.models.model_cost_budget import (
    ModelCostBudgetCreateSchema,
    ModelCostBudgetUpdateSchema,
)

router = APIRouter(prefix="/api/models/cost", tags=["cost-management"])


@router.get("/budgets")
async def list_budgets(
    workspace_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    service = CostBudgetService(db)
    budgets = await service.list_budgets(workspace_id)
    return [b.model_dump() for b in budgets]


@router.post("/budgets")
async def create_budget(
    data: ModelCostBudgetCreateSchema,
    workspace_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = CostBudgetService(db)
    budget = await service.create_budget(data, workspace_id)
    return budget.model_dump()


@router.put("/budgets/{budget_id}")
async def update_budget(
    budget_id: uuid.UUID,
    data: ModelCostBudgetUpdateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = CostBudgetService(db)
    budget = await service.update_budget(budget_id, data)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    return budget.model_dump()


@router.delete("/budgets/{budget_id}")
async def delete_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    service = CostBudgetService(db)
    deleted = await service.delete_budget(budget_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Budget not found")
    return {"status": "deleted"}


@router.get("/budgets/{budget_id}/status")
async def get_budget_status(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = CostBudgetService(db)
    status = await service.get_budget_status(budget_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    return status.model_dump()


@router.get("/anomalies")
async def get_anomalies(
    workspace_id: uuid.UUID | None = None,
    model_id: str | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    service = CostBudgetService(db)
    anomalies = await service.detect_anomalies(workspace_id, model_id)
    return [a.model_dump() for a in anomalies]


@router.get("/forecast")
async def get_forecast(
    workspace_id: uuid.UUID | None = None,
    budget_limit: float | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = CostBudgetService(db)
    forecast = await service.forecast_spend(workspace_id, budget_limit)
    return forecast.model_dump()


@router.get("/chargeback")
async def get_chargeback(
    workspace_id: uuid.UUID | None = None,
    dimension: str = Query("agent", pattern="^(agent|model|user)$"),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    service = CostBudgetService(db)
    entries = await service.generate_chargeback(workspace_id, dimension)
    return [e.model_dump() for e in entries]
