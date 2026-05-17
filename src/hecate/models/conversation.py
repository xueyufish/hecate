from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ConversationModel(BaseModel):
    """ORM model for conversations — groups messages under an agent."""

    __tablename__ = "conversations"

    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index(
            "idx_conversations_agent",
            "agent_id",
            postgresql_where=BaseModel.deleted_at.is_(None),
        ),
    )


class ConversationCreateSchema(PydanticBase):
    """Schema for creating a new conversation."""

    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    title: str | None = None


class ConversationReadSchema(PydanticBase):
    """Schema for reading conversation data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
