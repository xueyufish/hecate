"""Model Deployment ORM model and Pydantic schemas for lifecycle management.

Defines staging channels (dev/staging/prod) with approval workflows,
deprecation scheduling, and rollback support.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ModelDeploymentModel(BaseModel):
    """ORM model for model deployments across staging channels.

    Each row represents a model deployment in a specific channel
    (dev/staging/prod) with approval status and lifecycle tracking.

    Inherits id, created_at, updated_at, deleted, deleted_at from BaseModel.
    """

    __tablename__ = "model_deployments"

    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    deployment_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="approved")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    deprecated_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    sunset_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index(
            "uq_model_deployments_model_channel",
            "model_id",
            "channel",
            "deleted",
            "deleted_at",
            unique=True,
        ),
        Index("idx_model_deployments_model", "model_id", "deleted"),
        Index("idx_model_deployments_channel", "channel", "deleted"),
    )


class ModelDeploymentCreateSchema(PydanticBase):
    """Schema for creating a new model deployment."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1, max_length=255)
    channel: str = Field(..., pattern="^(dev|staging|prod)$")
    version: str | None = Field(None, max_length=50)
    deployment_config: dict = Field(default_factory=dict)
    workspace_id: uuid.UUID | None = None


class ModelDeploymentReadSchema(PydanticBase):
    """Schema for reading model deployment data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model_id: str
    channel: str
    version: str | None
    deployment_config: dict
    approval_status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    deprecated_at: datetime | None
    sunset_at: datetime | None
    is_enabled: bool
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PromotionRequestSchema(PydanticBase):
    """Schema for model promotion request."""

    model_config = ConfigDict(extra="forbid")

    from_channel: str = Field(..., pattern="^(dev|staging)$")
    to_channel: str = Field(..., pattern="^(staging|prod)$")


class DeprecationRequestSchema(PydanticBase):
    """Schema for model deprecation request."""

    model_config = ConfigDict(extra="forbid")

    sunset_at: datetime


class RollbackRequestSchema(PydanticBase):
    """Schema for model rollback request."""

    model_config = ConfigDict(extra="forbid")

    to_version: str = Field(..., min_length=1, max_length=50)
