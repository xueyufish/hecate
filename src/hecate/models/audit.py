"""Audit log ORM model, action enum, and Pydantic schemas.

Defines the persistence layer for enterprise-grade audit logging:

- **AuditAction** — StrEnum covering 6 modules (auth, agent, workflow, knowledge, tool, system)
- **AuditLogModel** — Immutable log record with multi-tenant scope
- **AuditLogQuerySchema** — Filter parameters for querying audit logs
"""

# ruff: noqa: S105  — "password" appears in audit action identifiers, not secrets
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel

# ---------------------------------------------------------------------------
# Action Enum — 6 modules
# ---------------------------------------------------------------------------


class AuditAction(enum.StrEnum):
    """Audit action identifiers organized by module.

    Format: ``<module>.<resource>.<verb>`` or ``<module>.<verb>``.
    """

    # AUTH module
    AUTH_LOGIN_SUCCESS = "auth.login.success"
    AUTH_LOGIN_FAILURE = "auth.login.failure"
    AUTH_LOGOUT = "auth.logout"
    AUTH_PASSWORD_CHANGE = "auth.password.change"
    AUTH_API_KEY_CREATE = "auth.api_key.create"
    AUTH_API_KEY_REVOKE = "auth.api_key.revoke"
    AUTH_PERMISSION_GRANT = "auth.permission.grant"
    AUTH_PERMISSION_REVOKE = "auth.permission.revoke"

    # AGENT module
    AGENT_CREATE = "agent.create"
    AGENT_UPDATE = "agent.update"
    AGENT_DELETE = "agent.delete"
    AGENT_PUBLISH = "agent.publish"
    AGENT_EXECUTE_START = "agent.execute.start"
    AGENT_EXECUTE_COMPLETE = "agent.execute.complete"
    AGENT_EXECUTE_ERROR = "agent.execute.error"
    AGENT_CONFIG_CHANGE = "agent.config.change"

    # WORKFLOW module
    WORKFLOW_CREATE = "workflow.create"
    WORKFLOW_UPDATE = "workflow.update"
    WORKFLOW_DELETE = "workflow.delete"
    WORKFLOW_EXECUTE_START = "workflow.execute.start"
    WORKFLOW_EXECUTE_COMPLETE = "workflow.execute.complete"
    WORKFLOW_VERSION_PUBLISH = "workflow.version.publish"
    WORKFLOW_VERSION_ROLLBACK = "workflow.version.rollback"

    # KNOWLEDGE module
    KNOWLEDGE_BASE_CREATE = "knowledge.base.create"
    KNOWLEDGE_BASE_DELETE = "knowledge.base.delete"
    KNOWLEDGE_DOCUMENT_UPLOAD = "knowledge.document.upload"
    KNOWLEDGE_DOCUMENT_DELETE = "knowledge.document.delete"
    KNOWLEDGE_QUERY = "knowledge.query"

    # TOOL module
    TOOL_REGISTER = "tool.register"
    TOOL_UPDATE = "tool.update"
    TOOL_DEREGISTER = "tool.deregister"
    TOOL_EXECUTE_START = "tool.execute.start"
    TOOL_EXECUTE_COMPLETE = "tool.execute.complete"
    TOOL_EXECUTE_ERROR = "tool.execute.error"

    # SYSTEM module
    SYSTEM_USER_CREATE = "system.user.create"
    SYSTEM_USER_UPDATE = "system.user.update"
    SYSTEM_USER_DELETE = "system.user.delete"
    SYSTEM_WORKSPACE_CREATE = "system.workspace.create"
    SYSTEM_WORKSPACE_DELETE = "system.workspace.delete"
    SYSTEM_SETTINGS_UPDATE = "system.settings.update"
    SYSTEM_RATE_LIMIT_TRIGGERED = "system.rate_limit.triggered"
    SYSTEM_MODEL_PROVIDER_CREATE = "system.model_provider.create"
    SYSTEM_MODEL_PROVIDER_DELETE = "system.model_provider.delete"


# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------


class AuditLogModel(BaseModel):
    """ORM model for audit log records.

    Audit logs are append-only (no updates or deletes by design).
    Inherits id, created_at, updated_at from BaseModel but deliberately
    does NOT use soft-delete — audit records are never logically deleted.

    Key fields:

    - **org_id** — organization scope (required for multi-tenant isolation)
    - **workspace_id** — optional workspace scope
    - **user_id** — the actor performing the action
    - **action** — dot-notation action identifier from AuditAction
    - **resource_type** / **resource_id** — what was acted upon
    - **success** — whether the action succeeded
    - **metadata_** — JSONB for arbitrary context (request body hash, etc.)
    """

    __tablename__ = "audit_logs"

    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    request_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    request_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("idx_audit_logs_org_created", "org_id", "created_at"),
        Index("idx_audit_logs_workspace_action", "workspace_id", "action", "created_at"),
        Index("idx_audit_logs_user_created", "user_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class AuditLogReadSchema(PydanticBase):
    """Schema for reading audit log records."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID | None
    user_id: uuid.UUID
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    request_method: str | None
    request_path: str | None
    response_status: int | None
    ip_address: str | None
    user_agent: str | None
    success: bool
    error_code: str | None
    error_message: str | None
    metadata: dict | None = Field(validation_alias="metadata_")
    created_at: datetime


class AuditLogQuerySchema(PydanticBase):
    """Schema for querying/filtering audit logs."""

    model_config = ConfigDict(extra="forbid")

    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    success: bool | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
