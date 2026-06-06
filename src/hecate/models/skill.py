"""Skill ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for skills — reusable
instruction sets that extend an agent's capabilities. Each skill stores
the content of a SKILL.md file along with associated tools and metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class SkillModel(BaseModel):
    """ORM model for skills — stores SKILL.md instructions and associated resources.

    Key fields:

    - **source** — origin of the skill: ``"system"`` (built-in, shipped with
      Hecate), ``"user"`` (created by an end-user), or ``"project"``
      (project-level, shared across agents in a workspace).
    - **instructions** — the main skill content, typically the full text of
      a ``SKILL.md`` file that defines the skill's behaviour and prompts.
    - **allowed_tools** — list of tool names this skill is permitted to
      invoke at runtime.
    - **metadata_** — SQLAlchemy attribute ``metadata_`` mapping to column
      ``metadata`` (see :class:`SessionModel` for the alias rationale).
    - **scripts** — associated executable scripts referenced by the skill.
    - **references** — supplementary reference materials (URLs, document
      IDs, etc.).
    - **max_tokens** — token budget for the skill's instructions when
      injected into the agent context.
    - **auto_load** — if ``True``, the skill is automatically injected into
      the agent's system prompt at session start, without requiring
      explicit user selection.
    """

    __tablename__ = "skills"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    instructions: Mapped[str] = mapped_column(nullable=False)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    scripts: Mapped[list] = mapped_column(JSON, default=list)
    references: Mapped[list] = mapped_column(JSON, default=list)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2000)
    auto_load: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index(
            "idx_skills_workspace_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=BaseModel.deleted_at.is_(None),
        ),
    )


class SkillCreateSchema(PydanticBase):
    """Schema for creating a new skill."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9-]*$")
    description: str
    source: str = Field(..., pattern="^(system|user|project)$")
    instructions: str
    allowed_tools: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    scripts: list = Field(default_factory=list)
    references: list = Field(default_factory=list)
    max_tokens: int = 2000
    auto_load: bool = False


class SkillUpdateSchema(PydanticBase):
    """Schema for updating an existing skill. All fields are optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9-]*$")
    description: str | None = None
    instructions: str | None = None
    allowed_tools: list | None = None
    metadata: dict | None = None
    scripts: list | None = None
    references: list | None = None
    max_tokens: int | None = None
    auto_load: bool | None = None


class SkillReadSchema(PydanticBase):
    """Schema for reading skill data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str
    source: str
    instructions: str
    allowed_tools: list
    metadata: dict = Field(validation_alias="metadata_")
    scripts: list
    references: list
    max_tokens: int
    auto_load: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
