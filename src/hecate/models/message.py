"""Message ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for messages — individual
turns within a conversation, including assistant responses with tool calls
and the corresponding tool results.
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


class MessageModel(BaseModel):
    """ORM model for messages — stores conversation messages with optional tool calls.

    Key fields:

    - **role** — message origin: ``"system"``, ``"user"``, ``"assistant"``,
      or ``"tool"``.
    - **tool_calls** — when the assistant invokes tools, this JSONB column
      stores a list of call descriptors with the shape:
      ``[{"id": "call_xxx", "function": {"name": "...", "arguments": "..."}}]``.
      ``None`` for non-assistant messages or assistant messages without tool
      use.
    - **tool_call_id** — for ``"tool"`` role messages, this field links the
      tool result back to the corresponding ``tool_calls[].id`` from the
      preceding assistant message.
    - **metadata_** — SQLAlchemy attribute ``metadata_`` mapping to column
      ``metadata`` (see :class:`SessionModel` for the alias rationale).
    """

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(nullable=False)
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (Index("idx_messages_conversation", "conversation_id", "created_at"),)


class MessageCreateSchema(PydanticBase):
    """Schema for creating a new message."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID
    role: str = Field(..., pattern="^(system|user|assistant|tool)$")
    content: str
    tool_calls: list | None = None
    tool_call_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class MessageReadSchema(PydanticBase):
    """Schema for reading message data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    tool_calls: list | None
    tool_call_id: str | None
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime
