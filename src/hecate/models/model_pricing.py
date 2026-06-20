"""Model pricing ORM model and Pydantic schemas for cost tracking.

Defines the persistence layer for model pricing with time-ranged validity,
enabling accurate cost calculation from token usage data.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ModelPricingModel(BaseModel):
    """ORM model for model pricing entries with time-ranged validity.

    Each row represents a pricing entry for a specific model, effective
    from ``effective_from`` until ``effective_until`` (NULL means current).

    Inherits id, created_at, updated_at, deleted, deleted_at from BaseModel.
    """

    __tablename__ = "model_pricings"

    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_price_per_1k: Mapped[float] = mapped_column(Float, nullable=False)
    output_price_per_1k: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    effective_from: Mapped[datetime] = mapped_column(nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index(
            "idx_model_pricings_model",
            "model_id",
            "workspace_id",
            "deleted",
        ),
        Index(
            "idx_model_pricings_effective",
            "effective_from",
            "effective_until",
        ),
    )


class ModelPricingCreateSchema(PydanticBase):
    """Schema for creating a new model pricing entry."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=255)
    input_price_per_1k: float = Field(..., ge=0)
    output_price_per_1k: float = Field(..., ge=0)
    currency: str = "USD"
    effective_from: datetime


class ModelPricingUpdateSchema(PydanticBase):
    """Schema for updating a model pricing entry. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    input_price_per_1k: float | None = Field(None, ge=0)
    output_price_per_1k: float | None = Field(None, ge=0)
    currency: str | None = None
    effective_until: datetime | None = None


class ModelPricingReadSchema(PydanticBase):
    """Schema for reading model pricing data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_id: str
    display_name: str
    input_price_per_1k: float
    output_price_per_1k: float
    currency: str
    effective_from: datetime
    effective_until: datetime | None = None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None = None


class CostSummarySchema(PydanticBase):
    """Schema for cost summary response."""

    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    unpriced_tokens: int = 0
    currency: str = "USD"


class CostBreakdownEntrySchema(PydanticBase):
    """Schema for a single cost breakdown entry."""

    key: str
    cost: float
    input_tokens: int
    output_tokens: int
    percentage: float


class CostTimeseriesPointSchema(PydanticBase):
    """Schema for a single cost timeseries data point."""

    timestamp: datetime
    cost: float
    input_tokens: int
    output_tokens: int
