"""Approval record ORM model and Pydantic schemas.

Persists tool execution approval decisions for scope-based caching
(SESSION, PROJECT, GLOBAL).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ApprovalRecordModel(BaseModel):
    """ORM model for tool execution approval records.

    Stores approval decisions for scope-based caching. When a tool is
    approved with scope=SESSION, subsequent calls in the same session
    can reuse this record. scope=PROJECT persists across sessions within
    a workspace.
    """

    __tablename__ = "approval_records"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    scope: Mapped[str] = mapped_column(String(20), default="once")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "idx_approval_workspace_tool",
            "workspace_id",
            "tool_name",
            "deleted",
            "deleted_at",
        ),
    )


class ApprovalCreateSchema(PydanticBase):
    """Schema for creating a new approval record."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    tool_name: str
    session_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    scope: str = "once"
    risk_level: str
    reason: str | None = None
    expires_at: datetime | None = None


class ApprovalReadSchema(PydanticBase):
    """Schema for reading approval record data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    tool_name: str
    session_id: uuid.UUID | None
    user_id: uuid.UUID | None
    scope: str
    status: str
    risk_level: str
    reason: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
