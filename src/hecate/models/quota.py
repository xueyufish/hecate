"""Quota ORM models and Pydantic schemas for per-tenant resource limits.

Defines 2 ORM tables (quotas, quota_usage), 4 StrEnums (resource type, scope,
window type, enforcement mode), and Pydantic CRUD schemas for API validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field, computed_field
from sqlalchemy import Boolean, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class QuotaResourceType(StrEnum):
    """Resources that can be quota-limited."""

    REQUESTS = "requests"
    TOKENS = "tokens"
    COST = "cost"


class QuotaScope(StrEnum):
    """Quota scoping levels."""

    WORKSPACE = "workspace"
    API_KEY = "api_key"


class QuotaWindowType(StrEnum):
    """Quota reset window types."""

    ROLLING_MINUTE = "rolling_minute"
    DAILY = "daily"
    MONTHLY = "monthly"


class EnforcementMode(StrEnum):
    """Quota enforcement behavior when limit is exceeded."""

    HARD_REJECT = "hard_reject"
    SOFT_ALLOW = "soft_allow"


class QuotaModel(BaseModel):
    """ORM model for quota definitions."""

    __tablename__ = "quotas"
    __table_args__ = (Index("ix_quotas_workspace_scope_resource", "workspace_id", "scope", "resource_type"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    soft_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    window_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enforcement: Mapped[str] = mapped_column(String(16), nullable=False, default=EnforcementMode.HARD_REJECT)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class QuotaUsageModel(BaseModel):
    """ORM model for quota usage tracking within a period."""

    __tablename__ = "quota_usage"
    __table_args__ = (Index("ix_quota_usage_quota_period", "quota_id", "period_start"),)

    quota_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(nullable=False)
    period_end: Mapped[datetime] = mapped_column(nullable=False)
    used_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_updated: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    soft_limit_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class QuotaCreateSchema(PydanticBase):
    """Schema for creating a quota definition."""

    name: str = Field(..., min_length=1, max_length=255)
    resource_type: QuotaResourceType
    scope: QuotaScope
    scope_id: uuid.UUID
    limit_value: float = Field(..., gt=0)
    soft_limit: float | None = Field(None, gt=0)
    window_type: QuotaWindowType
    enforcement: EnforcementMode = EnforcementMode.HARD_REJECT
    enabled: bool = True


class QuotaUpdateSchema(PydanticBase):
    """Schema for updating a quota definition."""

    name: str | None = Field(None, min_length=1, max_length=255)
    limit_value: float | None = Field(None, gt=0)
    soft_limit: float | None = Field(None, gt=0)
    enforcement: EnforcementMode | None = None
    enabled: bool | None = None


class QuotaReadSchema(PydanticBase):
    """Schema for reading a quota definition."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    resource_type: str
    scope: str
    scope_id: uuid.UUID
    limit_value: float
    soft_limit: float | None = None
    window_type: str
    enforcement: str
    enabled: bool = True
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class QuotaUsageReadSchema(PydanticBase):
    """Schema for reading quota usage with computed fields."""

    model_config = ConfigDict(from_attributes=True)

    quota_id: uuid.UUID
    name: str
    resource_type: str
    limit_value: float
    used_value: float
    soft_limit: float | None = None
    period_start: datetime
    period_end: datetime
    enforcement: str = EnforcementMode.HARD_REJECT

    @computed_field
    @property
    def remaining(self) -> float:
        """Remaining quota value."""
        return max(0.0, self.limit_value - self.used_value)

    @computed_field
    @property
    def utilization_pct(self) -> float:
        """Utilization percentage."""
        if self.limit_value <= 0:
            return 0.0
        return round((self.used_value / self.limit_value) * 100, 2)
