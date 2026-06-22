"""Alert ORM models and Pydantic schemas for the alerting system.

Defines 5 ORM tables (alert rules, events, silences, escalation policies,
notification channels), 4 StrEnums (alert type, state, severity, channel type),
and Pydantic CRUD schemas for API validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class AlertType(StrEnum):
    """Canonical alert signal types."""

    ERROR_RATE = "error_rate"
    LATENCY_P95 = "latency_p95"
    LATENCY_TTFT = "latency_ttft"
    TOKEN_USAGE = "token_usage"
    COST_DAILY = "cost_daily"
    COST_MONTHLY_FORECAST = "cost_monthly_forecast"
    TOOL_FAILURE_RATE = "tool_failure_rate"
    SUCCESS_RATE = "success_rate"
    EVALUATION_REGRESSION = "evaluation_regression"


class AlertState(StrEnum):
    """Alert event lifecycle states."""

    PENDING = "pending"
    FIRING = "firing"
    RESOLVED = "resolved"
    ACKED = "acked"


class AlertSeverity(StrEnum):
    """Alert severity levels for routing and escalation."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class ChannelType(StrEnum):
    """Notification channel types."""

    WEBHOOK_FEISHU = "webhook_feishu"
    WEBHOOK_WECOM = "webhook_wecom"
    WEBHOOK_DINGTALK = "webhook_dingtalk"
    WEBHOOK_SLACK = "webhook_slack"
    WEBHOOK_GENERIC = "webhook_generic"
    WEBSOCKET = "websocket"
    EMAIL = "email"


class AlertRuleModel(BaseModel):
    """ORM model for alert rule definitions."""

    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_workspace_enabled", "workspace_id", "enabled"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    for_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default=AlertSeverity.WARNING)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    escalation_policy_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    channel_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class AlertEventModel(BaseModel):
    """ORM model for alert event instances."""

    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_events_workspace_state", "workspace_id", "state"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=AlertState.PENDING)
    current_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fired_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    acked_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    escalation_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class AlertSilenceModel(BaseModel):
    """ORM model for alert silence windows."""

    __tablename__ = "alert_silences"

    start_at: Mapped[datetime] = mapped_column(nullable=False)
    end_at: Mapped[datetime] = mapped_column(nullable=False)
    matchers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class EscalationPolicyModel(BaseModel):
    """ORM model for reusable escalation policies."""

    __tablename__ = "escalation_policies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    repeat_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class NotificationChannelModel(BaseModel):
    """ORM model for notification channel configurations."""

    __tablename__ = "notification_channels"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000")
    )


class AlertRuleCreateSchema(PydanticBase):
    """Schema for creating an alert rule."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=500)
    alert_type: AlertType
    threshold: float = Field(..., ge=0)
    window_minutes: int = Field(5, ge=1, le=10080)
    for_minutes: int = Field(2, ge=0, le=10080)
    severity: AlertSeverity = AlertSeverity.WARNING
    filters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    escalation_policy_id: uuid.UUID | None = None
    channel_ids: list[uuid.UUID] = Field(default_factory=list)


class AlertRuleUpdateSchema(PydanticBase):
    """Schema for updating an alert rule."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=500)
    threshold: float | None = Field(None, ge=0)
    window_minutes: int | None = Field(None, ge=1, le=10080)
    for_minutes: int | None = Field(None, ge=0, le=10080)
    severity: AlertSeverity | None = None
    filters: dict[str, Any] | None = None
    enabled: bool | None = None
    escalation_policy_id: uuid.UUID | None = None
    channel_ids: list[uuid.UUID] | None = None


class AlertRuleReadSchema(PydanticBase):
    """Schema for reading an alert rule."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    alert_type: str
    threshold: float
    window_minutes: int
    for_minutes: int
    severity: str
    filters: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    escalation_policy_id: uuid.UUID | None = None
    channel_ids: list[Any] = Field(default_factory=list)
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AlertEventReadSchema(PydanticBase):
    """Schema for reading an alert event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID
    state: str
    current_value: float
    fired_at: datetime | None = None
    resolved_at: datetime | None = None
    acked_at: datetime | None = None
    acked_by: uuid.UUID | None = None
    escalation_step: int = 0
    workspace_id: uuid.UUID
    created_at: datetime


class AlertSilenceCreateSchema(PydanticBase):
    """Schema for creating a silence window."""

    start_at: datetime
    end_at: datetime
    matchers: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(None, max_length=500)


class AlertSilenceReadSchema(PydanticBase):
    """Schema for reading a silence window."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    start_at: datetime
    end_at: datetime
    matchers: dict[str, Any] = Field(default_factory=dict)
    created_by: uuid.UUID | None = None
    reason: str | None = None
    workspace_id: uuid.UUID
    created_at: datetime


class EscalationPolicyCreateSchema(PydanticBase):
    """Schema for creating an escalation policy."""

    name: str = Field(..., min_length=1, max_length=255)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    repeat_interval_min: int | None = Field(None, ge=1)


class EscalationPolicyUpdateSchema(PydanticBase):
    """Schema for updating an escalation policy."""

    name: str | None = Field(None, min_length=1, max_length=255)
    steps: list[dict[str, Any]] | None = None
    repeat_interval_min: int | None = Field(None, ge=1)


class EscalationPolicyReadSchema(PydanticBase):
    """Schema for reading an escalation policy."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    steps: list[Any] = Field(default_factory=list)
    repeat_interval_min: int | None = None
    workspace_id: uuid.UUID
    created_at: datetime


class NotificationChannelCreateSchema(PydanticBase):
    """Schema for creating a notification channel."""

    name: str = Field(..., min_length=1, max_length=255)
    channel_type: ChannelType
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class NotificationChannelUpdateSchema(PydanticBase):
    """Schema for updating a notification channel."""

    name: str | None = Field(None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class NotificationChannelReadSchema(PydanticBase):
    """Schema for reading a notification channel."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    channel_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    workspace_id: uuid.UUID
    created_at: datetime
