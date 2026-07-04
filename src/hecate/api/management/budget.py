"""Budget management API — CRUD, status with forecast, and chargeback reports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.budget.budget_service import BudgetService
from hecate.core.database import get_db
from hecate.models.quota import QuotaModel, QuotaResourceType

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


class BudgetCreateSchema(PydanticBase):
    """Schema for creating a budget."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    scope: str
    scope_id: uuid.UUID
    limit_value: float = Field(..., gt=0)
    soft_limit: float | None = Field(None, gt=0)
    window_type: str = "monthly"
    enforcement: str = "hard_reject"


class BudgetReadSchema(PydanticBase):
    """Schema for reading budget data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    scope: str
    scope_id: uuid.UUID
    resource_type: str
    limit_value: float
    soft_limit: float | None
    window_type: str
    enforcement: str
    enabled: bool


class BudgetUpdateSchema(PydanticBase):
    """Schema for updating a budget."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    limit_value: float | None = Field(None, gt=0)
    soft_limit: float | None = None
    enabled: bool | None = None


class ChargebackRequest(PydanticBase):
    """Schema for chargeback report request."""

    model_config = ConfigDict(extra="forbid")

    scope: str
    scope_id: uuid.UUID
    group_by: str = Field(..., pattern="^(agent|workspace|model)$")
    start_date: datetime
    end_date: datetime


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_budget(
    data: BudgetCreateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Create a new budget."""
    quota = QuotaModel(
        name=data.name,
        resource_type=QuotaResourceType.COST,
        scope=data.scope,
        scope_id=data.scope_id,
        limit_value=data.limit_value,
        soft_limit=data.soft_limit,
        window_type=data.window_type,
        enforcement=data.enforcement,
        enabled=True,
    )
    db.add(quota)
    await db.flush()
    return BudgetReadSchema.model_validate(quota).model_dump()


@router.get("")
async def list_budgets(
    scope: str | None = Query(None),
    scope_id: uuid.UUID | None = Query(None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict]:
    """List budgets with optional scope filter."""
    conditions = [
        QuotaModel.resource_type == QuotaResourceType.COST,
        QuotaModel.deleted.is_(False),
    ]
    if scope:
        conditions.append(QuotaModel.scope == scope)
    if scope_id:
        conditions.append(QuotaModel.scope_id == scope_id)

    result = await db.execute(select(QuotaModel).where(*conditions))
    quotas = result.scalars().all()
    return [BudgetReadSchema.model_validate(q).model_dump() for q in quotas]


@router.get("/{budget_id}")
async def get_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Get a budget by ID."""
    result = await db.execute(select(QuotaModel).where(QuotaModel.id == budget_id, QuotaModel.deleted.is_(False)))
    quota = result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    return BudgetReadSchema.model_validate(quota).model_dump()


@router.get("/{budget_id}/status")
async def get_budget_status(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Get budget status with forecast."""
    result = await db.execute(select(QuotaModel).where(QuotaModel.id == budget_id, QuotaModel.deleted.is_(False)))
    quota = result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(status_code=404, detail="Budget not found")

    service = BudgetService(db)
    utilization = await service.get_utilization(quota.scope, quota.scope_id)
    forecast = await service.forecast_remaining(quota.scope, quota.scope_id)

    return {
        "budget": BudgetReadSchema.model_validate(quota).model_dump(),
        "utilization": utilization,
        "forecast": forecast,
    }


@router.put("/{budget_id}")
async def update_budget(
    budget_id: uuid.UUID,
    data: BudgetUpdateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Update a budget."""
    result = await db.execute(select(QuotaModel).where(QuotaModel.id == budget_id, QuotaModel.deleted.is_(False)))
    quota = result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(status_code=404, detail="Budget not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(quota, key, value)

    await db.flush()
    return BudgetReadSchema.model_validate(quota).model_dump()


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a budget."""
    result = await db.execute(select(QuotaModel).where(QuotaModel.id == budget_id, QuotaModel.deleted.is_(False)))
    quota = result.scalar_one_or_none()
    if quota is None:
        raise HTTPException(status_code=404, detail="Budget not found")

    quota.deleted = True
    quota.deleted_at = datetime.now(UTC)
    await db.flush()


@router.post("/chargeback")
async def get_chargeback(
    data: ChargebackRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict]:
    """Get chargeback report."""
    service = BudgetService(db)
    return await service.create_chargeback(
        scope=data.scope,
        scope_id=data.scope_id,
        group_by=data.group_by,
        start_date=data.start_date,
        end_date=data.end_date,
    )
