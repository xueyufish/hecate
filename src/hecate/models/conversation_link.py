"""Conversation-to-task link ORM model - lazy legacy session compatibility.

One row associates a chat conversation with a platform Task (plan step4:
one conversation may produce several tasks). Links are created lazily by
the execution-domain registry the first time a legacy session touches the
task-run flow - never by a data migration replaying history, and never by
re-executing anything. Rows missing resolvable initiator governance data
are marked ``governance_status=pending`` instead of being assigned a
fabricated platform identity.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class GovernanceStatus(StrEnum):
    """Whether the link's governance data resolved cleanly."""

    OK = "ok"
    PENDING = "pending"


class ConversationTaskLinkModel(BaseModel):
    """One conversation ↔ task association (plan step4).

    Fields:

    - **conversation_id** — the platform conversation (chat session) id;
      unique together with ``task_id`` so replays cannot double-link.
    - **task_id** — the task this conversation produced.
    - **workspace_id** — copied from the task for isolation-scoped reads.
    - **governance_status** — ``ok`` when the initiator chain resolved to
      governed data, ``pending`` when the record was created from a legacy
      session lacking resolvable governance data; pending rows never
      auto-promote, an operator remediation does.
    """

    __tablename__ = "conversation_task_links"
    __table_args__ = (
        UniqueConstraint("conversation_id", "task_id", name="uq_conversation_task"),
        UniqueConstraint("task_id", name="uq_conversation_task_task"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    governance_status: Mapped[GovernanceStatus] = mapped_column(
        String(16),
        nullable=False,
        default=GovernanceStatus.OK,
    )
