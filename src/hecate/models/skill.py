from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class SkillModel(BaseModel):
    """ORM model for skills — stores SKILL.md instructions and associated resources."""

    __tablename__ = "skills"

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
            "idx_skills_name",
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


class SkillReadSchema(PydanticBase):
    """Schema for reading skill data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    source: str
    instructions: str
    allowed_tools: list
    metadata: dict
    scripts: list
    references: list
    max_tokens: int
    auto_load: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
