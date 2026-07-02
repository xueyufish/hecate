"""Session ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for sessions, which track the
execution state of an agent within a conversation. Sessions support
interrupt/resume workflows via checkpoints and graph-node tracking.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class SessionModel(BaseModel):
    """ORM model for sessions — tracks execution state within a conversation.

    Key fields:

    - **status** — session lifecycle state: ``"active"`` (currently running),
      ``"interrupted"`` (paused for human input), ``"completed"`` (finished
      normally), or ``"error"`` (terminated due to failure).
    - **current_node** — the graph node identifier where the session is
      currently paused. Used by the interrupt/resume mechanism to restore
      execution at the correct position in the workflow graph.
    - **checkpoint_id** — references the latest :class:`CheckpointModel` for
      this session, enabling state recovery and time-travel debugging.
    - **metadata_** — SQLAlchemy attribute named ``metadata_`` that maps to
      the database column ``metadata``. The trailing underscore avoids a
      conflict with SQLAlchemy's reserved ``metadata`` attribute on
      ``DeclarativeBase``. The corresponding Pydantic read schema uses
      ``validation_alias="metadata_"`` to map back to the Python attribute
      name ``metadata``.
    """

    __tablename__ = "sessions"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    current_node: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_sessions_agent", "agent_id"),
        Index("idx_sessions_conversation", "conversation_id"),
        Index("idx_sessions_workspace", "workspace_id", "deleted"),
    )


class SessionCreateSchema(PydanticBase):
    """Schema for creating a new session."""

    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None


class SessionReadSchema(PydanticBase):
    """Schema for reading session data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID | None
    agent_id: uuid.UUID
    status: str
    current_node: str | None
    checkpoint_id: uuid.UUID | None
    workspace_id: uuid.UUID
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime
