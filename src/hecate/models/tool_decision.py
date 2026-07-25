"""Tool decision audit event ORM model and Pydantic schemas.

Stores structured tool policy decision events emitted by ToolPolicyPipeline,
ToolAccessPolicy, and SandboxEnforcementRouter. Each event captures the
full decision context: tool name, arguments hash, decision, reason,
policy version, per-layer breakdown, and actor attribution.

The async batch writer (ToolDecisionService) flushes events to this
table every AGENT_ENV_DECISION_BATCH_SIZE events or AGENT_ENV_DECISION_FLUSH_INTERVAL
seconds, whichever comes first.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ToolDecisionModel(BaseModel):
    """ORM table for structured tool policy decision events.

    Records every policy evaluation in the tool execution pipeline.
    Designed for compliance auditing and the SIEM export pipeline.
    Uses async batch writing to amortize I/O cost.
    """

    __tablename__ = "tool_decisions"
    __table_args__ = (
        Index("ix_tool_decision_agent_ts", "agent_id", "timestamp"),
        Index("ix_tool_decision_workspace_ts", "workspace_id", "timestamp"),
        Index("ix_tool_decision_decision_ts", "decision", "timestamp"),
    )

    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    on_behalf_of_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    layer_results: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    # Override the timestamp from BaseModel's created_at to use our own
    # indexed field for time-range queries.
    timestamp: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"ToolDecisionModel(id={self.id}, agent_id={self.agent_id}, "
            f"tool={self.tool_name}, decision={self.decision})"
        )


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ToolDecisionReadSchema(PydanticBase):
    """Schema for reading a tool decision event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: str
    workspace_id: str
    session_id: str | None = None
    tool_name: str
    arguments_hash: str
    decision: str
    reason: str
    policy_version: str
    on_behalf_of_user: str | None = None
    layer_results: list[dict] = Field(default_factory=list)
    timestamp: datetime


class ToolDecisionQuerySchema(PydanticBase):
    """Query parameters for filtering tool decision events."""

    agent_id: str | None = None
    workspace_id: str | None = None
    session_id: str | None = None
    decision: str | None = None
    tool_name: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
