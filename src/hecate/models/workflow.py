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
    """

    __tablename__ = "workflows"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_workflows_workspace", "workspace_id", postgresql_where=BaseModel.deleted_at.is_(None)),
    )


class WorkflowVersionModel(BaseModel):
    """ORM model for workflow versions — immutable snapshots of graph definitions.

    Key fields:

    - **workflow_id** — references the parent WorkflowModel.
    - **version** — monotonically increasing version number.
    - **graph_dsl** — JSONB column storing the raw graph DSL definition.
    - **compiled_graph** — JSONB column storing the compiled graph output.
    - **change_summary** — optional description of what changed in this version.

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

    __table_args__ = (
        Index("idx_workflow_versions_workflow", "workflow_id"),
        Index("idx_workflow_versions_unique", "workflow_id", "version", unique=True),
    )


# --- Pydantic Schemas ---


class WorkflowCreateSchema(PydanticBase):
    """Schema for creating a new workflow."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    graph_dsl: dict[str, Any] = Field(..., description="Graph DSL definition")
    change_summary: str = Field(default="", max_length=1000)


class WorkflowUpdateSchema(PydanticBase):
    """Schema for updating an existing workflow."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    graph_dsl: dict[str, Any] | None = Field(None, description="Updated graph DSL definition")
    change_summary: str = Field(default="", max_length=1000)


class WorkflowReadSchema(PydanticBase):
    """Schema for reading workflow data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    current_version: int
    created_at: datetime
    updated_at: datetime
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
    created_at: datetime


class WorkflowDetailSchema(PydanticBase):
    """Schema for reading workflow with current version details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    current_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: WorkflowVersionReadSchema | None = None
