"""Tool policy ORM model and Pydantic schemas.

Stores workspace-level tool security rules (deny/ask/allow) with glob
pattern matching for tool names.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import JSON, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ToolPolicyModel(BaseModel):
    """ORM model for workspace-level tool security policies.

    Each rule maps a tool-name glob pattern to an action (allow/deny/ask).
    Workspace admins set deny rules as a security baseline that cannot be
    overridden by agent-level guardrail_config rules.
    """

    __tablename__ = "tool_policies"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    rule_action: Mapped[str] = mapped_column(String(20), nullable=False)
    tool_pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    arg_conditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "idx_tool_policy_workspace_pattern_action",
            "workspace_id",
            "tool_pattern",
            "rule_action",
            "deleted",
            "deleted_at",
            unique=True,
        ),
    )


class ToolPolicyCreateSchema(PydanticBase):
    """Schema for creating a new tool policy rule."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    rule_action: str = Field(..., pattern="^(allow|deny|ask)$")
    tool_pattern: str = Field(..., min_length=1, max_length=255)
    priority: int = 0
    description: str | None = None
    arg_conditions: dict[str, str] | None = None


class ToolPolicyReadSchema(PydanticBase):
    """Schema for reading tool policy data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    rule_action: str
    tool_pattern: str
    priority: int
    description: str | None
    arg_conditions: dict[str, str] | None = None
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
