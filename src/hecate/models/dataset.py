"""Dataset ORM model and Pydantic schemas for fine-tuning data management."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class DatasetModel(BaseModel):
    """ORM model for fine-tuning datasets."""

    __tablename__ = "datasets"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="jsonl")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_preview: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    file_storage_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_datasets_workspace", "workspace_id", "deleted"),
        Index("idx_datasets_name", "name", "workspace_id", "deleted"),
    )


class DatasetCreateSchema(PydanticBase):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    format: str = Field(default="jsonl", pattern="^(jsonl|csv|json)$")
    workspace_id: uuid.UUID | None = None


class DatasetReadSchema(PydanticBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    format: str
    version: int
    row_count: int
    schema_preview: dict
    file_storage_url: str | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
