"""Workspace ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for workspaces,
the resource isolation boundary in the multi-tenant hierarchy.
Each workspace belongs to exactly one organization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel, JSONType


class WorkspaceModel(BaseModel):
    """ORM model for workspaces — the resource isolation boundary.

    Each workspace belongs to an organization. All tenant-scoped resources
    (agents, workflows, skills, tools, knowledge bases, prompts, memories)
    belong to exactly one workspace.
    """

    __tablename__ = "workspaces"

    org_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    default_locale: Mapped[str] = mapped_column(String(10), nullable=False, server_default="en")
    settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
        default=None,
    )

    __table_args__ = (
        Index("idx_workspaces_org_slug", "org_id", "slug", unique=True),
        Index("idx_workspaces_org", "org_id"),
    )


class WorkspaceCreateSchema(PydanticBase):
    """Schema for creating a workspace."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkspaceUpdateSchema(PydanticBase):
    """Schema for updating a workspace."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    settings: dict[str, Any] | None = None


class WorkspaceReadSchema(PydanticBase):
    """Schema for reading workspace data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    settings: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
