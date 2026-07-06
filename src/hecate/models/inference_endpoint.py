"""Inference endpoint ORM model and Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class InferenceEndpointModel(BaseModel):
    """ORM model for registered inference endpoints."""

    __tablename__ = "inference_endpoints"

    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    backend_type: Mapped[str] = mapped_column(String(50), nullable=False, default="openai-compatible")
    auth_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    health_status: Mapped[str] = mapped_column(String(20), nullable=False, default="healthy")
    last_health_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_inference_endpoints_model", "model_id", "deleted"),
        Index("idx_inference_endpoints_workspace", "workspace_id", "deleted"),
    )


class InferenceEndpointCreateSchema(PydanticBase):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1, max_length=1024)
    model_id: str = Field(..., min_length=1, max_length=255)
    backend_type: str = Field(default="openai-compatible", pattern="^(vllm|ollama|openai-compatible|custom)$")
    auth_config: dict = Field(default_factory=dict)
    workspace_id: uuid.UUID | None = None


class InferenceEndpointReadSchema(PydanticBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    model_id: str
    backend_type: str
    health_status: str
    last_health_at: datetime | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
