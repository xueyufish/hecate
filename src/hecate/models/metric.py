"""Metric ORM model and Pydantic schemas for real-time monitoring.

Defines the persistence layer and API schemas for time-series metrics,
supporting both counter and gauge metric types with tag-based filtering.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel as PydanticBase
from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class MetricType(StrEnum):
    """Standard metric type categories."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class MetricModel(BaseModel):
    """ORM model for time-series metric records.

    Each row represents a single metric data point with a name, value,
    type, and optional tags for dimensional querying.

    Inherits id, created_at, updated_at, deleted, deleted_at from BaseModel.
    """

    __tablename__ = "metrics"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default=MetricType.COUNTER)
    tags: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    timestamp: Mapped[datetime] = mapped_column(nullable=False, index=True)


class MetricCreateSchema(PydanticBase):
    """Schema for creating a metric data point."""

    name: str
    value: float
    type: str = MetricType.COUNTER
    tags: dict[str, Any] | None = None
    timestamp: datetime | None = None


class MetricQuerySchema(PydanticBase):
    """Schema for querying aggregated metrics."""

    name: str
    window: str = "5m"
    aggregation: str = "sum"
    tags: dict[str, Any] | None = None


class MetricAggregateSchema(PydanticBase):
    """Schema for a single aggregated metric result."""

    name: str
    value: float
    aggregation: str = "sum"
    window: str = "5m"
    tags: dict[str, Any] | None = None
    count: int = 0
    timestamp: datetime | None = None


class MetricsSnapshotSchema(PydanticBase):
    """Schema for a snapshot of all current metrics."""

    metrics: list[MetricAggregateSchema]
    timestamp: datetime
    window: str = "5m"
