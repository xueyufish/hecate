"""Agent version ORM model and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for
agent versions — immutable snapshots of an agent's own configuration
(1.3.20). Each commit freezes the live agent row into a version record
with:

- ``config_snapshot`` — the frozen own-config fields.
- ``pinned_refs`` — workflow references pinned to a specific workflow
  version at commit time.
- ``ref_manifest`` — references to not-yet-versioned resources (tools,
  skills, knowledge bases) recorded with content hashes so drift stays
  detectable while resolution remains live.

Versions are immutable once created; the ``agents.published_version``
pointer decides which version external channels serve.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class AgentVersionModel(BaseModel):
    """ORM model for agent versions — immutable snapshots of agent configs.

    Key fields:

    - **agent_id** — references the parent AgentModel.
    - **version** — monotonically increasing version number, unique per
      agent.
    - **config_snapshot** — JSONB column freezing the agent's own config
      fields (persona, model_config, mode, tools, skills, knowledge
      base ids, risk level, guardrail config, ...).
    - **pinned_refs** — JSONB list of pinned references, currently
      ``[{"resource_type": "workflow", "resource_id": ..., "version": n}]``.
    - **ref_manifest** — JSONB list of manifest entries for unversioned
      resources: ``[{"resource_type", "resource_id", "version": null,
      "content_hash"}]``.
    - **schema_version** — snapshot schema version for forward-compatible
      resolution (resolvers ignore unknown fields).
    - **content_hash** — sha256 over the canonical JSON of
      ``config_snapshot``; used to derive the "uncommitted changes" badge
      without diffing full snapshots.

    Note: This model inherits BaseModel but versions are immutable — once
    created, config_snapshot / pinned_refs / ref_manifest never change.
    """

    __tablename__ = "agent_versions"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pinned_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ref_manifest: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_agent_versions_agent", "agent_id", "deleted"),
        Index("idx_agent_versions_workspace", "workspace_id", "deleted"),
    )


# --- Pydantic Schemas ---


class AgentVersionCommitSchema(PydanticBase):
    """Request body for committing a new agent version."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=255, description="Display name for the version")
    change_summary: str = Field(default="", max_length=2000, description="What changed in this version")


class AgentVersionUpdateSchema(PydanticBase):
    """Request body for renaming a version / editing its release notes."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=0, max_length=255)
    change_summary: str | None = Field(None, max_length=2000)


class AgentVersionReadSchema(PydanticBase):
    """Schema for reading an agent version (list/detail without snapshot)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    version: int
    name: str
    change_summary: str
    content_hash: str
    is_published: bool | None = None
    created_by: uuid.UUID | None = None
    workspace_id: uuid.UUID
    created_at: datetime


class AgentVersionDetailSchema(AgentVersionReadSchema):
    """Schema for reading an agent version with its full snapshot."""

    schema_version: int
    config_snapshot: dict[str, Any]
    pinned_refs: list[dict[str, Any]]
    ref_manifest: list[dict[str, Any]]


class AgentVersionDiffSchema(PydanticBase):
    """Schema for the diff between two agent versions."""

    model_config = ConfigDict(extra="forbid")

    v1: int
    v2: int
    identical: bool
    summary: dict[str, Any]
    details: dict[str, Any]


class AgentVersionStatusSchema(PydanticBase):
    """Badge status for an agent: versions, published pointer, dirty flag."""

    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    latest_version: int | None
    published_version: int | None
    has_uncommitted_changes: bool


class AgentVersionDriftSchema(PydanticBase):
    """Drift report for one version's reference manifest.

    ``drifted`` lists manifest entries whose live content hash no longer
    matches the hash recorded at commit time (or whose resource has
    disappeared). An empty list means the snapshot's references are
    content-identical to what is live right now.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    version: int
    drifted: list[dict[str, Any]]


class AgentPublicationModel(BaseModel):
    """ORM model for append-only agent publication receipts (step11).

    One row per successful publish: the publication-time evidence identity
    (which candidate/baseline evaluation runs the gate actually consumed,
    anchored by their ``agent_config_hash``), the gate configuration and
    report snapshots, and the outcome. The record is written in the same
    transaction as the ``agents.published_version`` pointer move;
    each successful publish appends a receipt, including republication.

    Key fields:

    - **agent_id** / **version** — what was published.
    - **agent_config_hash** — aligns with the version's ``content_hash``
      so evidence identity survives run soft-deletion.
    - **candidate_run_id** / **candidate_run_hash** — the gate's actual
      candidate evaluation run and its config hash (NULL when the gate was
      off).
    - **baseline_run_id** / **baseline_run_hash** — the baseline run used
      for regression comparison (NULL when no published version existed or
      regression comparison was disabled or the gate was off).
    - **gate_config** — the agent's ``evaluation_gate`` snapshot at
      publish time (NULL when unconfigured).
    - **gate_report** — the rendered gate report snapshot (a minimal
      ``mode=off`` report when disabled; never carries "evaluation passed"
      semantics for ``gate_off`` outcomes).
    - **outcome** — closed enum: ``passed`` / ``warn_passed`` /
      ``force_bypassed`` / ``gate_off``. Enforced by a database constraint
      and the read schema.

    Append-only by design: later task, gate-config or evaluation changes
    never rewrite a receipt; rollback does not create one (the pointer did
    not move). Publications made before this model existed have no receipt
    and are never backfilled.
    """

    __tablename__ = "agent_publications"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    candidate_run_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    baseline_run_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gate_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    gate_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    operator_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('passed', 'warn_passed', 'force_bypassed', 'gate_off')",
            name="ck_agent_publications_outcome",
        ),
        Index("idx_agent_publications_agent", "agent_id", "created_at"),
        Index("idx_agent_publications_workspace", "workspace_id", "agent_id"),
    )


class AgentPublicationReadSchema(PydanticBase):
    """Whitelisted publication receipt for API reads."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    version: int
    agent_config_hash: str
    candidate_run_id: uuid.UUID | None = None
    candidate_run_hash: str | None = None
    baseline_run_id: uuid.UUID | None = None
    baseline_run_hash: str | None = None
    gate_config: dict[str, Any] | None = None
    gate_report: dict[str, Any] | None = None
    outcome: Literal["passed", "warn_passed", "force_bypassed", "gate_off"]
    operator_user_id: uuid.UUID | None = None
    created_at: datetime
