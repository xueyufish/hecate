"""Hook configuration ORM model and Pydantic schemas.

Stores hook configurations for session events, tool matchers, and shell commands.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class HookConfigModel(BaseModel):
    """ORM model for hook configurations.

    Attributes:
        workspace_id: Workspace this hook belongs to.
        agent_id: Agent this hook belongs to (None = workspace-level).
        event: Hook event type (SessionStart, SessionEnd, UserPromptSubmit,
               PreCompact, PreToolUse, PostToolUse).
        matcher: Tool name pattern for tool hooks (None = match all).
        command: Shell command to execute.
        timeout: Max execution time in seconds.
        enabled: Whether this hook is active.
    """

    __tablename__ = "hook_configs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    matcher: Mapped[str | None] = mapped_column(String(500), nullable=True)
    command: Mapped[str] = mapped_column(String(1000), nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (Index("idx_hook_configs_ws_agent", "workspace_id", "agent_id"),)


class HookConfigCreateSchema(PydanticBase):
    """Schema for creating a hook configuration."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = None
    event: str = Field(..., min_length=1, max_length=50)
    matcher: str | None = None
    command: str = Field(..., min_length=1, max_length=1000)
    timeout: int = Field(default=30, ge=1, le=300)
    enabled: bool = True


class HookConfigReadSchema(PydanticBase):
    """Schema for reading hook configuration data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID | None
    event: str
    matcher: str | None
    command: str
    timeout: int
    enabled: bool
    created_at: datetime
    updated_at: datetime
