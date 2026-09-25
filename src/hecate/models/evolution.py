"""Evolution loop ORM models and schemas.

Persistence for the self-evolution closed loop (1.3.6f): learning inputs
harvested from completed conversations, evolution runs that batch-attribute
those inputs, and candidate skills awaiting validation and human review.

Behaviour contract: ``openspec/changes/skill-evolution-loop/specs/``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class EvolutionSignalType(StrEnum):
    """Why a conversation was harvested as a learning input."""

    QUALITY_BELOW_THRESHOLD = "quality_below_threshold"
    USER_CORRECTION = "user_correction"


class EvolutionInputStatus(StrEnum):
    """Lifecycle of a harvested learning input."""

    PENDING = "pending"
    SKIPPED = "skipped"
    ATTRIBUTED = "attributed"
    FAILED = "failed"


class EvolutionRunStatus(StrEnum):
    """State machine for an evolution run."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    CANDIDATES_READY = "candidates_ready"
    GATED = "gated"
    PUBLISHED = "published"
    CONCLUDED = "concluded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"


class SkillCandidateStatus(StrEnum):
    """Lifecycle of a candidate skill through gate and review."""

    PENDING = "pending"
    VALIDATING = "validating"
    VALIDATED = "validated"
    # Gate passed its content checks but behavioral evaluation was skipped
    # (no eval runner wired) — reviewable, but not claimed behavior-validated.
    CONTENT_VALIDATED = "content_validated"
    VALIDATION_FAILED = "validation_failed"
    INSUFFICIENT_DATA = "insufficient_data"
    BLOCKED = "blocked"
    PUBLISHED = "published"
    REJECTED = "rejected"


class EvolutionInputModel(BaseModel):
    """One harvested conversation that may carry a learnable failure.

    Created asynchronously after conversation completion when a quality
    signal fires; never blocks the chat response path.
    """

    __tablename__ = "evolution_learning_inputs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    signal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=EvolutionInputStatus.PENDING.value)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evolution_runs.id", ondelete="SET NULL"), nullable=True, default=None
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (Index("idx_evolution_inputs_workspace_status", "workspace_id", "status"),)


class EvolutionRunModel(BaseModel):
    """One scheduled batch attribution run over accumulated learning inputs."""

    __tablename__ = "evolution_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=EvolutionRunStatus.PENDING.value)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("idx_evolution_runs_workspace_status", "workspace_id", "status"),)


class SkillCandidateModel(BaseModel):
    """A learned, knowledge-only skill candidate awaiting gate and review.

    Content is partitioned into ``procedure`` (what to do) and
    ``guardrails`` (what to avoid) — failure lessons land as guardrails
    instead of standalone constraint records. Updates to an existing theme
    are appended as structured deltas, never full rewrites.
    """

    __tablename__ = "skill_candidates"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evolution_runs.id", ondelete="SET NULL"), nullable=True, default=None
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    procedure: Mapped[str] = mapped_column(Text, nullable=False, default="")
    guardrails: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failure_category: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    deltas: Mapped[list] = mapped_column(JSON, default=list)
    validation_report: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    scan_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    scan_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=SkillCandidateStatus.PENDING.value)
    review_decision: Mapped[str | None] = mapped_column(String(30), nullable=True, default=None)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    edit_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    published_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"), nullable=True, default=None
    )

    __table_args__ = (
        Index("idx_skill_candidates_workspace_status", "workspace_id", "status"),
        Index("idx_skill_candidates_workspace_name", "workspace_id", "name"),
    )


class SkillUsageEventModel(BaseModel):
    """One skill usage observation (L1 catalog served / L2 content loaded).

    Written best-effort by the SkillLoader; powers the effect-feedback
    statistics (trigger counts, per-skill session quality comparison) and
    the 5.9c discovery-provenance split (bound vs auto_detected loads).
    """

    __tablename__ = "skill_usage_events"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    skill_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    skill_name: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 5.9c: "bound" (explicit binding or auto_load path) / "auto_detected"
    # (discovered pool). NULL on pre-5.9c rows is read as "bound".
    detected_via: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)

    __table_args__ = (Index("idx_skill_usage_workspace_skill", "workspace_id", "skill_name", "created_at"),)


class EvolutionGoldenSubsetModel(BaseModel):
    """One frozen golden-subset sample for an agent (regression baseline).

    Golden samples are conversations with real captured evidence, frozen so
    candidate-skill validation can assert "no regression on a stable set".
    """

    __tablename__ = "evolution_golden_subsets"

    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    __table_args__ = (Index("idx_golden_subset_agent", "workspace_id", "agent_id"),)


class EvolutionInputCreateSchema(PydanticBase):
    """Schema for creating a learning input (internal harvest path)."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    signal_type: EvolutionSignalType
    quality_score: float | None = None
    detail: dict = {}


class SkillCandidateReviewSchema(PydanticBase):
    """Reviewer decision on a validated candidate."""

    model_config = ConfigDict(extra="forbid")

    decision: str  # approved | rejected | approved_with_edits
    reviewer: str
    comment: str | None = None
    edited_content: dict | None = None  # optional {name, description, procedure, guardrails}
