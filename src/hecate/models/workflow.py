"""Workflow ORM models and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for
workflows — directed graphs that define agent execution flows. Workflows
are versioned: each save creates an immutable version with the graph DSL
and compiled graph.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class WorkflowModel(BaseModel):
    """ORM model for workflows — the top-level workflow entity.

    Key fields:

    - **workspace_id** — tenant scope. Defaults to the zero UUID for P1
      single-workspace mode; reserved for P3 multi-tenancy support.
    - **name** — human-readable workflow name.
    - **current_version** — the latest version number (auto-incremented).
    - **execution_mode** — ``"conversational"`` for multi-turn workflows,
      ``"task"`` for single-shot headless execution.
    - **published_version** — version number currently published to production,
      or ``None`` if never published.
    """

    __tablename__ = "workflows"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="conversational")
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    __table_args__ = (Index("idx_workflows_workspace", "workspace_id", "deleted"),)


class WorkflowVersionModel(BaseModel):
    """ORM model for workflow versions — immutable snapshots of graph definitions.

    Key fields:

    - **workflow_id** — references the parent WorkflowModel.
    - **version** — monotonically increasing version number.
    - **graph_dsl** — JSONB column storing the raw graph DSL definition.
    - **compiled_graph** — JSONB column storing the compiled graph output.
    - **change_summary** — optional description of what changed in this version.
    - **labels** — deployment labels (e.g., ``["production"]``, ``["staging"]``).

    Note: This model inherits BaseModel but versions are immutable — once
    created, graph_dsl and compiled_graph never change.
    """

    __tablename__ = "workflow_versions"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_dsl: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    compiled_graph: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_workflow_versions_workflow", "workflow_id"),
        Index("idx_workflow_versions_unique", "workflow_id", "version", unique=True),
        Index("idx_workflow_versions_workspace", "workspace_id", "deleted"),
    )


class WorkflowRunModel(BaseModel):
    """ORM model for workflow test run history.

    Persists test run results so users can review past execution outcomes,
    including per-node status, timing, and errors.

    Key fields:

    - **workflow_id** — references the parent WorkflowModel.
    - **run_id** — unique identifier for this test run (generated at execution time).
    - **status** — execution status: ``completed`` or ``error``.
    - **mock** — whether the run used mock LLM responses.
    - **input_data** — the input payload used for the run.
    - **node_results** — JSON array of per-node execution results.
    - **total_duration_ms** — total wall-clock time in milliseconds.
    - **error** — error message if the run failed.
    """

    __tablename__ = "workflow_runs"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    mock: Mapped[bool] = mapped_column(nullable=False, default=True)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    node_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    total_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_workflow_runs_workflow", "workflow_id"),
        Index("idx_workflow_runs_workspace", "workspace_id", "deleted"),
    )


# --- Pydantic Schemas ---


class WorkflowCreateSchema(PydanticBase):
    """Schema for creating a new workflow."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    graph_dsl: dict[str, Any] = Field(..., description="Graph DSL definition")
    change_summary: str = Field(default="", max_length=1000)
    execution_mode: str = Field(default="conversational", pattern="^(conversational|task)$")


class WorkflowUpdateSchema(PydanticBase):
    """Schema for updating an existing workflow."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    graph_dsl: dict[str, Any] | None = Field(None, description="Updated graph DSL definition")
    change_summary: str = Field(default="", max_length=1000)
    execution_mode: str | None = Field(None, pattern="^(conversational|task)$")


class WorkflowReadSchema(PydanticBase):
    """Schema for reading workflow data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    current_version: int
    execution_mode: str
    published_version: int | None
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None


class WorkflowVersionReadSchema(PydanticBase):
    """Schema for reading workflow version data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version: int
    graph_dsl: dict[str, Any]
    compiled_graph: dict[str, Any]
    change_summary: str
    labels: list[str]
    workspace_id: uuid.UUID
    created_at: datetime


class WorkflowDetailSchema(PydanticBase):
    """Schema for reading workflow with current version details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    current_version: int
    execution_mode: str
    published_version: int | None
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
    version: WorkflowVersionReadSchema | None = None


class WorkflowRunReadSchema(PydanticBase):
    """Schema for reading workflow test run data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    mock: bool
    input_data: dict[str, Any]
    node_results: list[dict[str, Any]]
    total_duration_ms: int
    error: str | None
    workspace_id: uuid.UUID
    created_at: datetime
