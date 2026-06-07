"""Evidence ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for evidence records — structured
captures of tool execution results with provenance tracking, normalization, and
importance scoring for Context Engineering.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class EvidenceModel(BaseModel):
    """ORM model for evidence — structured tool execution results.

    Key fields:

    - **session_id** — the session that produced this evidence.
    - **conversation_id** — the conversation context.
    - **message_id** — the specific message that triggered the tool call.
    - **tool_name** — name of the tool that produced this evidence.
    - **tool_arguments** — JSONB column storing the arguments passed to the tool.
    - **raw_content** — the original raw output from the tool.
    - **normalized_content** — JSONB column with structured/normalized result.
    - **is_error** — whether this evidence represents an error result.
    - **importance** — importance score (0.0 to 1.0) for context prioritization.
    - **source_type** — type of evidence source (e.g. "tool", "knowledge", "user").
    - **provenance** — JSONB column with full provenance chain metadata.
    """

    __tablename__ = "evidences"

    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    source_type: Mapped[str] = mapped_column(String(50), default="tool")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("idx_evidences_session", "session_id"),
        Index("idx_evidences_tool", "tool_name"),
        Index("idx_evidences_importance", "importance"),
    )


class EvidenceCreateSchema(PydanticBase):
    """Schema for creating a new evidence record."""

    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    tool_name: str = Field(..., min_length=1, max_length=255)
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    raw_content: str | None = None
    normalized_content: dict[str, Any] = Field(default_factory=dict)
    is_error: bool = False
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source_type: str = Field(default="tool", max_length=50)
    provenance: dict[str, Any] = Field(default_factory=dict)


class EvidenceReadSchema(PydanticBase):
    """Schema for reading evidence data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    tool_name: str
    tool_arguments: dict[str, Any]
    raw_content: str | None
    normalized_content: dict[str, Any]
    is_error: bool
    importance: float
    source_type: str
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
