"""A2A Task ORM model for persisting A2A task lifecycle."""

from __future__ import annotations

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class A2ATaskModel(BaseModel):
    """ORM model for A2A tasks — persists task lifecycle and state transitions.

    Key fields:
    - task_id: Unique A2A task identifier (UUID string).
    - context_id: Conversation context ID grouping related tasks.
    - state: Current task state (submitted, working, completed, failed, canceled).
    - status_message: JSON dict of the last status message.
    - artifacts: JSON list of task artifacts.
    - history: JSON list of conversation history messages.
    """

    __tablename__ = "a2a_tasks"

    task_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    context_id: Mapped[str] = mapped_column(String(36), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    status_message: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (Index("idx_a2a_tasks_task_id", "task_id"),)
