"""Approval record ORM model and Pydantic schemas.

Persists tool execution approval decisions for scope-based caching
(SESSION, PROJECT, GLOBAL).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import JSON, Boolean, DateTime, Float, Index, String, Text
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
    # step7 projection columns — derived state only. The authoritative
    # binding/consumption record lives in ``approval_authority``; this table
    # is rebuilt from the event log (rebuild_approval_projection) and never
    # arbitrates consumption.
    approval_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    arguments_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decision_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        Index(
            "idx_approval_workspace_tool",
            "workspace_id",
            "tool_name",
            "deleted",
            "deleted_at",
        ),
    )


class ApprovalAuthorityModel(BaseModel):
    """Authoritative approval binding (step7).

    One row per ``approval_ref``. Judgment (pending/approved/rejected) and
    consumption state are separate fields; one-time consumption atomically
    claims the record by setting ``consumed_by_invocation_id``. Revocation
    and expiry are checked at dispatch — this table, not the projection,
    arbitrates.
    """

    __tablename__ = "approval_authority"

    approval_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    principal_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    deployment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    domains: Mapped[list | None] = mapped_column(JSON, nullable=True)
    task_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anchor_wall: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    anchor_monotonic: Mapped[float | None] = mapped_column(Float, nullable=True)
    clock_epoch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    one_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    judgment: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed_by_invocation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (Index("idx_approval_authority_tool", "tool_name", "judgment", "revoked"),)


class ActionClaimModel(BaseModel):
    """Claim/receipt record for ledger-less dispatch paths (step7).

    ``invocation_key`` uniquely identifies a logical call for its entire
    lifetime. Clean failures may be reclaimed by a conditional update of
    the same row; completed successes always replay the recorded result.
    """

    __tablename__ = "action_claims"

    invocation_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    side_effect_class: Mapped[str] = mapped_column(String(40), nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[Any | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "uq_action_claims_open",
            "invocation_key",
            unique=True,
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
