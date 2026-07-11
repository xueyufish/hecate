"""Conversation ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for conversations, which group
messages under an agent. Each session has a 1:1 relationship with a
conversation via ``session.conversation_id → conversation.id``.

When a session is created with ``conversation_id = None``, a conversation is
created automatically and linked back to the session.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ConversationModel(BaseModel):
    """ORM model for conversations — groups messages under an agent.

    A conversation represents a single dialogue thread between a user and an
    agent. It has a 1:1 relationship with a session
    (``session.conversation_id → conversation.id``).

    When a session is created without an existing ``conversation_id``, a new
    conversation is created automatically and linked to the session.

    Key fields:

    - **agent_id** — the agent this conversation belongs to.
    - **title** — optional human-readable title for display in UIs.
    """

    __tablename__ = "conversations"

    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    # Quality scoring aggregates
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_min_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_scored_at: Mapped[datetime | None] = mapped_column(nullable=True)
    quality_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Topic classification
    topic: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Feedback summary
    feedback_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Cluster assignment
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)

    __table_args__ = (
        Index("idx_conversations_agent", "agent_id", "deleted"),
        Index("idx_conversations_workspace", "workspace_id", "deleted"),
        Index("idx_conversations_quality", "quality_score"),
        Index("idx_conversations_topic", "topic"),
    )


class ConversationCreateSchema(PydanticBase):
    """Schema for creating a new conversation."""

    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    title: str | None = None
    workspace_id: uuid.UUID | None = None


class ConversationReadSchema(PydanticBase):
    """Schema for reading conversation data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    title: str | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
