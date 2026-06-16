"""Scheduled task ORM models, enums, and Pydantic schemas.

Defines the persistence layer for cron-based task scheduling:

- **ScheduleState** — lifecycle states for a scheduled task
- **ExecutionStatus** — outcome of a single execution
- **TriggerType** — how an execution was initiated
- **ScheduledTaskModel** — the task definition with cron expression
- **ScheduledTaskExecutionModel** — individual execution records
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ScheduleState(enum.StrEnum):
    """Lifecycle states for a scheduled task."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class ExecutionStatus(enum.StrEnum):
    """Outcome of a single task execution."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class TriggerType(enum.StrEnum):
    """How an execution was initiated."""

    CRON = "cron"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class ScheduledTaskModel(BaseModel):
    """ORM model for scheduled task definitions.

    Stores a cron expression, target agent/workflow, execution config,
    and scheduling metadata like next_run_at and max_concurrent_runs.

    Key fields:

    - **cron_expression** — standard 5-field cron expression
    - **agent_id** / **workflow_id** — the target to execute (one required)
    - **state** — current lifecycle state
    - **max_concurrent_runs** — limit parallel executions
    - **catch_up** — whether to run missed schedules on resume
    """

    __tablename__ = "scheduled_tasks"

    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    execution_config: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=ScheduleState.ACTIVE.value)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    catch_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("idx_scheduled_tasks_org_state", "org_id", "state"),
        Index("idx_scheduled_tasks_workspace_state", "workspace_id", "state"),
        Index("idx_scheduled_tasks_next_run", "next_run_at"),
    )


class ScheduledTaskExecutionModel(BaseModel):
    """ORM model for individual scheduled task execution records.

    Each execution captures timing, status, and result summary.

    Key fields:

    - **task_id** — foreign key to :class:`ScheduledTaskModel`
    - **status** — outcome (success, failed, timeout, skipped)
    - **triggered_by** — "cron" or "manual"
    - **duration_ms** — wall-clock execution time
    """

    __tablename__ = "scheduled_task_executions"

    task_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ExecutionStatus.SUCCESS.value)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(20), nullable=False, default=TriggerType.CRON.value)

    __table_args__ = (
        Index("idx_task_executions_task_created", "task_id", "created_at"),
        Index("idx_task_executions_task_status", "task_id", "status"),
    )


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class ScheduledTaskCreateSchema(PydanticBase):
    """Schema for creating a new scheduled task."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    cron_expression: str = Field(..., min_length=9, max_length=100)
    agent_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None
    execution_config: dict | None = None
    max_concurrent_runs: int = Field(1, ge=1, le=10)
    catch_up: bool = False
    timezone: str = "UTC"


class ScheduledTaskUpdateSchema(PydanticBase):
    """Schema for updating a scheduled task. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    cron_expression: str | None = Field(None, min_length=9, max_length=100)
    execution_config: dict | None = None
    max_concurrent_runs: int | None = Field(None, ge=1, le=10)
    catch_up: bool | None = None
    timezone: str | None = None
    enabled: bool | None = None
    state: str | None = Field(None, pattern="^(active|paused|completed)$")


class ScheduledTaskReadSchema(PydanticBase):
    """Schema for reading scheduled task data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID | None
    name: str
    description: str | None
    cron_expression: str
    agent_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    execution_config: dict
    state: str
    max_concurrent_runs: int
    catch_up: bool
    timezone: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ScheduledTaskExecutionReadSchema(PydanticBase):
    """Schema for reading scheduled task execution records."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    started_at: datetime | None
    completed_at: datetime | None
    status: str
    result_summary: dict | None
    error_message: str | None
    duration_ms: int | None
    triggered_by: str
    created_at: datetime
