"""Trace ORM model and Pydantic schemas for full-chain observability.

Defines the persistence layer and API schemas for trace and span records,
using an observation-centric single-table model with self-referencing parent_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class SpanType(StrEnum):
    """Standard span type categories."""

    TRACE = "trace"
    SPAN = "span"
    GENERATION = "generation"
    TOOL = "tool"
    RETRIEVAL = "retrieval"


class SpanLevel(StrEnum):
    """Span severity level."""

    DEBUG = "DEBUG"
    DEFAULT = "DEFAULT"
    WARNING = "WARNING"
    ERROR = "ERROR"


class SpanStatus(StrEnum):
    """Span execution status."""

    STARTED = "started"
    COMPLETED = "completed"
    ERROR = "error"


class TraceModel(BaseModel):
    """ORM model for trace/span records.

    Uses a single-table observation-centric design (inspired by LangFuse v4).
    Each row is either a root trace (parent_id=NULL) or a child span.

    Inherits id, created_at, updated_at, deleted, deleted_at from BaseModel.
    """

    __tablename__ = "traces"

    trace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default=SpanLevel.DEFAULT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SpanStatus.STARTED)
    start_time: Mapped[datetime] = mapped_column(nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(nullable=True)


class TraceCreateSchema(PydanticBase):
    """Schema for creating a trace/span record."""

    trace_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    type: str
    name: str
    session_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    input_data: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")
    start_time: datetime


class TraceReadSchema(PydanticBase):
    """Schema for reading a trace/span record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trace_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    type: str
    name: str
    session_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = Field(default=None, serialization_alias="metadata_")
    usage: dict[str, Any] | None = None
    level: str = SpanLevel.DEFAULT
    status: str = SpanStatus.STARTED
    start_time: datetime
    end_time: datetime | None = None
    created_at: datetime


class TraceListSchema(PydanticBase):
    """Schema for listing trace root records."""

    model_config = ConfigDict(from_attributes=True)

    trace_id: uuid.UUID
    name: str
    status: str
    start_time: datetime
    end_time: datetime | None = None
    session_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    usage: dict[str, Any] | None = None


class TraceDetailSchema(PydanticBase):
    """Schema for trace detail with hierarchical span tree."""

    model_config = ConfigDict(from_attributes=True)

    trace: TraceReadSchema
    spans: list[TraceReadSchema]
