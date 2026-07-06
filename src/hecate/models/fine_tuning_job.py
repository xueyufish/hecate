"""Fine-tuning job ORM model and Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class FineTuningJobModel(BaseModel):
    """ORM model for fine-tuning jobs."""

    __tablename__ = "fine_tuning_jobs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    base_model: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="openai")
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result_model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_fine_tuning_jobs_dataset", "dataset_id", "deleted"),
        Index("idx_fine_tuning_jobs_status", "status", "deleted"),
        Index("idx_fine_tuning_jobs_workspace", "workspace_id", "deleted"),
    )


class FineTuningJobCreateSchema(PydanticBase):
    model_config = ConfigDict(extra="forbid")

    dataset_id: uuid.UUID
    base_model: str = Field(..., min_length=1, max_length=255)
    provider: str = Field(default="openai", max_length=50)
    config: dict = Field(default_factory=dict)
    workspace_id: uuid.UUID | None = None


class FineTuningJobReadSchema(PydanticBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    base_model: str
    provider: str
    provider_job_id: str | None
    status: str
    config: dict
    result_model_id: str | None
    metrics: dict
    error_message: str | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
