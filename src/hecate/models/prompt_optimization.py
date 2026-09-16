"""Prompt optimization ORM models and Pydantic schemas (6.19).

Defines the persistence layer for evaluation-dataset-driven prompt
self-optimization runs and their candidates.

A **run** pins a prompt version, an agent under test, and a named dataset
version, then iterates mutation rounds: candidate templates are rolled out
against the real agent via per-invocation prompt override, scored with the
evaluation engine, and gated before entering the human review pool. A
**candidate** is one generated template with its full evidence (gate report,
per-item results, reflection summary, usage).

Runs never mutate the production prompt; candidates become prompt versions
only through explicit human approval (see review service).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class PromptOptimizationRunStatus(enum.StrEnum):
    """Lifecycle of an optimization run."""

    CREATED = "created"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    CONCLUDED = "concluded"
    FAILED = "failed"


class PromptOptimizationStopReason(enum.StrEnum):
    """Why the optimization loop stopped iterating."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    MAX_ROUNDS = "max_rounds"
    NO_PROGRESS = "no_progress"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class PromptOptimizationCandidateStatus(enum.StrEnum):
    """State of a candidate template within a run."""

    REJECTED_MUTATION = "rejected_mutation"
    GATE_REJECTED = "gate_rejected"
    SCAN_BLOCKED = "scan_blocked"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"


class PromptOptimizationBudgetPreset(enum.StrEnum):
    """Named budget presets mapping to loop caps (design D8)."""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


# Budget preset caps: max rounds / mutation LLM calls / rollout item count
# (each rollout item is one agent_execute invocation, train + val combined).
BUDGET_PRESETS: dict[str, dict[str, int]] = {
    "light": {"max_rounds": 2, "mutation_call_limit": 10, "rollout_item_limit": 100},
    "medium": {"max_rounds": 5, "mutation_call_limit": 25, "rollout_item_limit": 400},
    "heavy": {"max_rounds": 10, "mutation_call_limit": 50, "rollout_item_limit": 1000},
}

DEFAULT_REFLECTION_MODEL = "gpt-4o-mini"
NO_PROGRESS_ROUNDS = 3
DEFAULT_SPLIT_RATIO = 0.8

# Terminal candidate statuses: every candidate in a run's review pool must
# reach one of these before the run can conclude.
TERMINAL_CANDIDATE_STATUSES = {
    PromptOptimizationCandidateStatus.REJECTED_MUTATION,
    PromptOptimizationCandidateStatus.GATE_REJECTED,
    PromptOptimizationCandidateStatus.SCAN_BLOCKED,
    PromptOptimizationCandidateStatus.PUBLISHED,
    PromptOptimizationCandidateStatus.REJECTED,
}


class PromptOptimizationRunModel(BaseModel):
    """ORM model for a prompt self-optimization run.

    Key fields:

    - **prompt_id** / **base_version** — optimization target and the version
      candidate deltas are measured against.
    - **agent_id** — the agent under test (rollout vehicle).
    - **dataset_version_id** — pinned named dataset version (7.3b); the
      run consumes its frozen items only.
    - **split_ratio** — train/validation cut of the frozen items.
    - **evaluator_configs** — JSON list of evaluator names.
    - **primary_metric** / **min_improvement** / **max_regression** —
      acceptance gate parameters (design D2).
    - **budget_preset** / **max_rounds** / **mutation_call_limit** /
      **rollout_item_limit** — effective caps (design D8).
    - **status** / **stop_reason** / **round_count** / **usage** — loop state
      and cumulative accounting.
    """

    __tablename__ = "prompt_optimization_runs"

    prompt_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_dataset_versions.id"),
        nullable=False,
    )
    dataset_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    split_ratio: Mapped[float] = mapped_column(nullable=False, default=DEFAULT_SPLIT_RATIO)
    evaluator_configs: Mapped[list] = mapped_column(JSON, default=list)
    primary_metric: Mapped[str] = mapped_column(String(100), nullable=False)
    min_improvement: Mapped[float] = mapped_column(nullable=False, default=0.02)
    max_regression: Mapped[float] = mapped_column(nullable=False, default=0.05)
    budget_preset: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    mutation_call_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    rollout_item_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="reflective_mutation")
    strategy_params: Mapped[dict] = mapped_column(JSON, default=dict)
    reflection_model: Mapped[str] = mapped_column(String(255), nullable=False, default=DEFAULT_REFLECTION_MODEL)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=PromptOptimizationRunStatus.CREATED.value,
        index=True,
    )
    stop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    round_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_prompt_opt_runs_prompt", "prompt_id", "status"),
        Index("idx_prompt_opt_runs_workspace", "workspace_id", "deleted"),
    )


class PromptOptimizationCandidateModel(BaseModel):
    """ORM model for one candidate template produced during a run.

    Status flow: every candidate starts as the strategy's output; the
    template integrity gate may mark it ``rejected_mutation`` (no rollout
    cost), the acceptance gate may mark it ``gate_rejected``, content
    scanning may mark it ``scan_blocked``, review may publish
    (``published``) or reject (``rejected``) it.
    """

    __tablename__ = "prompt_optimization_candidates"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_optimization_runs.id"),
        nullable=False,
        index=True,
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=PromptOptimizationCandidateStatus.REJECTED_MUTATION.value,
        index=True,
    )
    gate_report: Mapped[dict] = mapped_column(JSON, default=dict)
    per_item_results: Mapped[list] = mapped_column(JSON, default=list)
    reflection_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    rollout_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_prompt_opt_cand_run", "run_id", "status"),
        Index("idx_prompt_opt_cand_workspace", "workspace_id", "deleted"),
    )


# --- Pydantic Schemas ---


class PromptOptimizationRunCreateSchema(PydanticBase):
    """Schema for creating a prompt optimization run."""

    model_config = ConfigDict(extra="forbid")

    prompt_id: uuid.UUID
    base_version: int | None = None
    agent_id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_version_id: uuid.UUID
    split_ratio: float = Field(default=DEFAULT_SPLIT_RATIO, ge=0.1, le=0.9)
    evaluator_configs: list[str] = Field(min_length=1)
    primary_metric: str = Field(min_length=1, max_length=100)
    min_improvement: float = Field(default=0.02, ge=0.0, le=1.0)
    max_regression: float = Field(default=0.05, ge=0.0, le=1.0)
    budget_preset: PromptOptimizationBudgetPreset = PromptOptimizationBudgetPreset.MEDIUM
    max_rounds: int | None = Field(default=None, ge=1)
    mutation_call_limit: int | None = Field(default=None, ge=1)
    rollout_item_limit: int | None = Field(default=None, ge=1)
    strategy: str = "reflective_mutation"
    strategy_params: dict = Field(default_factory=dict)
    reflection_model: str | None = None


class PromptOptimizationRunReadSchema(PydanticBase):
    """Schema for reading a run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt_id: uuid.UUID
    base_version: int
    agent_id: uuid.UUID
    dataset_id: uuid.UUID
    dataset_version_id: uuid.UUID
    dataset_version_hash: str
    split_ratio: float
    evaluator_configs: list
    primary_metric: str
    min_improvement: float
    max_regression: float
    budget_preset: str
    max_rounds: int
    mutation_call_limit: int
    rollout_item_limit: int
    strategy: str
    strategy_params: dict
    reflection_model: str
    status: str
    stop_reason: str | None
    round_count: int
    usage: dict
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_by: uuid.UUID | None
    workspace_id: uuid.UUID
    created_at: datetime


class PromptOptimizationCandidateReadSchema(PydanticBase):
    """Schema for reading a candidate."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    round_no: int
    parent_candidate_id: uuid.UUID | None
    template: str
    status: str
    gate_report: dict
    per_item_results: list
    reflection_summary: str | None
    usage: dict
    rollout_count: int
    rejection_reason: str | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    workspace_id: uuid.UUID
    created_at: datetime


class PromptOptimizationRejectSchema(PydanticBase):
    """Schema for rejecting a candidate — reason is required."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
