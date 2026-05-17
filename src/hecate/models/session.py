from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import Index, String
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class SessionModel(BaseModel):
    """ORM model for sessions — tracks execution state within a conversation."""

    __tablename__ = "sessions"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )
    current_node: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("idx_sessions_agent", "agent_id"),
        Index("idx_sessions_conversation", "conversation_id"),
    )


class SessionCreateSchema(PydanticBase):
    """Schema for creating a new session."""

    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    conversation_id: uuid.UUID | None = None


class SessionReadSchema(PydanticBase):
    """Schema for reading session data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID | None
    agent_id: uuid.UUID
    status: str
    current_node: str | None
    checkpoint_id: uuid.UUID | None
    metadata: dict
    created_at: datetime
    updated_at: datetime
