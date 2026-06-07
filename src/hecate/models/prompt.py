"""Prompt ORM models and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for
prompts — versioned text templates used for LLM instructions.

Prompts are versioned: each update creates an immutable version snapshot.
Labels (production/staging/development) enable environment-specific deployment.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class PromptModel(BaseModel):
    """ORM model for prompts — the top-level prompt entity.

    Key fields:

    - **workspace_id** — tenant scope.
    - **name** — unique prompt name within workspace.
    - **current_version** — latest version number (auto-incremented).
    """

    __tablename__ = "prompts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_prompts_workspace", "workspace_id"),
        Index("idx_prompts_name", "workspace_id", "name", unique=True),
    )


class PromptVersionModel(BaseModel):
    """ORM model for prompt versions — immutable snapshots of prompt content.

    Key fields:

    - **prompt_id** — references the parent PromptModel.
    - **version** — monotonically increasing version number.
    - **template** — the prompt template text (Jinja2 format).
    - **variables** — JSONB list of template variable names.
    - **labels** — JSONB list of deployment labels (production/staging/development).
    """

    __tablename__ = "prompt_versions"

    prompt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompts.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)

    __table_args__ = (
        Index("idx_prompt_versions_prompt", "prompt_id"),
        Index("idx_prompt_versions_unique", "prompt_id", "version", unique=True),
    )


# --- Pydantic Schemas ---


class PromptCreateSchema(PydanticBase):
    """Schema for creating a new prompt."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    template: str = Field(..., min_length=1)
    variables: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class PromptUpdateSchema(PydanticBase):
    """Schema for updating an existing prompt."""

    model_config = ConfigDict(extra="forbid")

    template: str | None = Field(None, min_length=1)
    variables: list[str] | None = None
    labels: list[str] | None = None


class PromptReadSchema(PydanticBase):
    """Schema for reading prompt data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    current_version: int
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None


class PromptVersionReadSchema(PydanticBase):
    """Schema for reading prompt version data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt_id: uuid.UUID
    version: int
    template: str
    variables: list[str]
    labels: list[str]
    created_at: datetime


class PromptDetailSchema(PydanticBase):
    """Schema for reading prompt with current version details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    current_version: int
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
    version: PromptVersionReadSchema | None = None
