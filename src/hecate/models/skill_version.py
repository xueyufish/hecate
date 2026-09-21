"""Skill version ORM model and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for
skill versions — immutable snapshots of a skill's content fields (5.9d).
Each commit freezes the live skill row into a version record so skill
edits stop being lossy: agents can pin an exact version, self-evolution
publishes become auditable, and a bad edit can be rolled back.

Frozen fields (the snapshot): name, instructions, allowed_tools,
scripts, references, description, max_tokens. Governance fields
(auto_load, model_invocable, user_invocable, provider, trust_tier,
metadata) stay live — they are policy, not content, and are resolved
from the live row at load time.

``content_hash`` covers only the 5-field content set shared with
agent-version reference manifests (name, instructions, allowed_tools,
scripts, references), so hashes recorded in the two places stay
comparable; the snapshot columns' integrity rests on row immutability.

Versions are immutable once created. Deleting the live skill row does
not cascade to versions: agent snapshots pin specific versions and must
keep resolving after the source row is gone.
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


class SkillVersionModel(BaseModel):
    """ORM model for skill versions — immutable snapshots of skill content.

    Key fields:

    - **skill_id** — references the parent SkillModel. Not cascaded on
      delete: pinned versions outlive their source row.
    - **version** — monotonically increasing version number, unique per
      skill.
    - **config_snapshot** — JSON column freezing the skill's content
      fields (name, instructions, allowed_tools, scripts, references,
      description, max_tokens).
    - **schema_version** — snapshot schema version for forward-compatible
      resolution (resolvers ignore unknown fields).
    - **content_hash** — sha256 over the canonical JSON of the 5-field
      content set (same algorithm/field set as agent ref manifests).
    - **learned_run_id** — set only when the version was auto-committed
      by the self-evolution publish path; links the snapshot to the
      evolution run that produced it.
    - **created_by** — null means system-created (evolution auto-commit).

    Note: This model inherits BaseModel but versions are immutable —
    once created, config_snapshot / content_hash never change (only the
    display metadata ``name`` / ``change_summary`` are editable).
    """

    __tablename__ = "skill_versions"

    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    learned_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("uq_skill_versions_skill_version", "skill_id", "version", unique=True),
        Index("idx_skill_versions_workspace", "workspace_id", "deleted"),
    )


# --- Pydantic Schemas ---


class SkillVersionCommitSchema(PydanticBase):
    """Request body for committing a new skill version."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=255, description="Display name for the version")
    change_summary: str = Field(default="", max_length=2000, description="What changed in this version")


class SkillVersionUpdateSchema(PydanticBase):
    """Request body for renaming a version / editing its notes (metadata only)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=0, max_length=255)
    change_summary: str | None = Field(None, max_length=2000)


class SkillVersionReadSchema(PydanticBase):
    """Schema for reading a skill version (list/detail without snapshot)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    version: int
    name: str
    change_summary: str
    content_hash: str
    learned_run_id: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    workspace_id: uuid.UUID
    created_at: datetime


class SkillVersionDetailSchema(SkillVersionReadSchema):
    """Schema for reading a skill version with its full snapshot."""

    schema_version: int
    config_snapshot: dict[str, Any]


class SkillVersionDiffSchema(PydanticBase):
    """Schema for the diff between two skill versions."""

    model_config = ConfigDict(extra="forbid")

    v1: int
    v2: int
    identical: bool
    summary: dict[str, Any]
    details: dict[str, Any]


class SkillVersionStatusSchema(PydanticBase):
    """Badge status for a skill: latest version and dirty flag."""

    model_config = ConfigDict(extra="forbid")

    skill_id: uuid.UUID
    latest_version: int | None
    has_uncommitted_changes: bool
