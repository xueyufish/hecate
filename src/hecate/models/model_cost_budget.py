"""Model cost budget ORM model and Pydantic schemas.

Defines cost budget management for model spending — per-model and per-workspace
cost caps with configurable enforcement policy (alert or block).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ModelCostBudgetModel(BaseModel):
    """ORM model for model cost budgets.

    Supports hierarchical budgets at workspace, agent, and user levels.
    Agent budget overrides workspace; user budget overrides agent.
    """

    __tablename__ = "model_cost_budgets"

    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    limit_amount: Mapped[float] = mapped_column(Float, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    policy: Mapped[str] = mapped_column(String(10), nullable=False, default="alert")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_cost_budgets_workspace", "workspace_id", "deleted"),
        Index("idx_cost_budgets_scope_target", "scope", "target_id", "workspace_id", "deleted"),
    )


class ModelCostBudgetCreateSchema(PydanticBase):
    """Schema for creating a cost budget."""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(..., pattern="^(workspace|agent|user)$")
    target_id: uuid.UUID | None = None
    limit_amount: float = Field(..., gt=0)
    period: str = Field(default="monthly", pattern="^(daily|weekly|monthly)$")
    currency: str = Field(default="USD", max_length=10)
    policy: str = Field(default="alert", pattern="^(alert|block)$")
    workspace_id: uuid.UUID | None = None


class ModelCostBudgetUpdateSchema(PydanticBase):
    """Schema for updating a cost budget."""

    model_config = ConfigDict(extra="forbid")

    limit_amount: float | None = Field(None, gt=0)
    period: str | None = Field(None, pattern="^(daily|weekly|monthly)$")
    policy: str | None = Field(None, pattern="^(alert|block)$")


class ModelCostBudgetReadSchema(PydanticBase):
    """Schema for reading cost budget data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope: str
    target_id: uuid.UUID | None
    limit_amount: float
    period: str
    currency: str
    policy: str
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class BudgetStatusSchema(PydanticBase):
    """Schema for budget status response."""

    budget_id: uuid.UUID
    scope: str
    target_id: uuid.UUID | None
    limit_amount: float
    spent_amount: float
    remaining_amount: float
    utilization_pct: float
    period: str
    policy: str
    status_band: str


class SpendForecastSchema(PydanticBase):
    """Schema for spend forecast response."""

    projected_amount: float
    confidence_low: float
    confidence_high: float
    status: str
    overrun: float


class AnomalySchema(PydanticBase):
    """Schema for cost anomaly."""

    model_config = ConfigDict(extra="forbid")

    date: str
    model: str
    actual_spend: float
    expected_spend: float
    z_score: float
    severity: str


class ChargebackEntrySchema(PydanticBase):
    """Schema for chargeback report entry."""

    dimension: str
    value: str
    total_cost: float
    top_model: str
    period_comparison_pct: float
