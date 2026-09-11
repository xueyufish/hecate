"""Evaluation ORM models and Pydantic schemas.

Defines the persistence layer and API schemas for the evaluation system:

- **EvaluationDatasetModel** — named collection of test cases
- **EvaluationItemModel** — individual test case (query, expected answer, context)
- **EvaluationRunModel** — execution of evaluators against a dataset
- **EvaluationScoreModel** — individual score from one evaluator on one item
- **EvaluationTaskModel** — reusable evaluation task (offline batch / online sampler)
- **EvaluationTaskScoreModel** — target-typed score produced by an online task
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class RunStatus(enum.StrEnum):
    """Lifecycle states for an evaluation run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskType(enum.StrEnum):
    """Discriminator for evaluation task kinds."""

    OFFLINE = "offline"
    ONLINE = "online"


class TaskStatus(enum.StrEnum):
    """Lifecycle states for an evaluation task definition.

    Only online tasks use ``disabled`` semantics (stop sampling); offline
    tasks are always ``active`` definitions that produce runs on demand.
    """

    ACTIVE = "active"
    DISABLED = "disabled"


class TaskScoreStatus(enum.StrEnum):
    """Per-score outcome for online evaluation scoring."""

    COMPLETED = "completed"
    ERROR = "error"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class EvaluationDatasetModel(BaseModel):
    """ORM model for evaluation datasets — named collections of test cases.

    Key fields:

    - **name** — human-readable dataset name (e.g. "RAG smoke test v2")
    - **description** — optional longer description
    - **metadata_** — JSON column aliased to ``metadata`` in SQL, avoiding
      SQLAlchemy's reserved ``metadata`` attribute
    """

    __tablename__ = "evaluation_datasets"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_eval_datasets_created", "created_at"),
        Index("idx_eval_datasets_workspace", "workspace_id", "deleted"),
    )


class EvaluationItemModel(BaseModel):
    """ORM model for individual evaluation test cases.

    Each item belongs to a dataset and contains the ground-truth data
    needed for evaluation (query, expected answer, context).

    Key fields:

    - **dataset_id** — foreign key to :class:`EvaluationDatasetModel`
    - **query** — the user query / question (required, non-empty)
    - **expected_answer** — ground-truth answer for comparison
    - **context** — relevant context passages for RAG evaluation
    - **metadata_** — JSON column for arbitrary item-level metadata
    """

    __tablename__ = "evaluation_items"

    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_answer: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    context: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_eval_items_dataset", "dataset_id"),
        Index("idx_eval_items_workspace", "workspace_id", "deleted"),
    )


class EvaluationRunModel(BaseModel):
    """ORM model for evaluation run executions.

    A run applies a set of evaluators to every item in a dataset and
    records the aggregate results.

    Key fields:

    - **dataset_id** — the dataset that was evaluated
    - **task_id** — nullable link to the evaluation task that triggered
      this run; ``NULL`` for request-triggered runs via ``POST /runs``
    - **evaluator_configs** — JSON array of evaluator names + their configs
    - **status** — ``pending``, ``running``, ``completed``, or ``failed``
    - **summary** — nullable JSON: pass/fail aggregation computed for task
      runs when a ``threshold`` is configured
    - **started_at** / **completed_at** — timing markers for the run
    """

    __tablename__ = "evaluation_runs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evaluator_configs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.PENDING.value)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_eval_runs_dataset", "dataset_id"),
        Index("idx_eval_runs_task", "task_id"),
        Index("idx_eval_runs_workspace", "workspace_id", "deleted"),
    )


class EvaluationScoreModel(BaseModel):
    """ORM model for individual evaluation scores.

    Each score represents one metric evaluated on one dataset item within
    one run.

    Key fields:

    - **run_id** — the evaluation run this score belongs to
    - **item_id** — the specific dataset item that was evaluated
    - **metric_name** — the evaluator metric (e.g. "faithfulness")
    - **value** — the metric value (typically 0.0–1.0, -1.0 for errors)
    - **reasoning** — optional explanation from the evaluator
    - **source** — how the score was produced (``"llm_judge"``,
      ``"deterministic"``, ``"human"``)
    """

    __tablename__ = "evaluation_scores"

    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm_judge")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_eval_scores_run", "run_id"),
        Index("idx_eval_scores_item", "item_id"),
        Index("idx_eval_scores_workspace", "workspace_id", "deleted"),
    )


class EvaluationTaskModel(BaseModel):
    """ORM model for reusable evaluation task definitions (7.2c).

    A task is a persistent evaluation configuration, separate from any run:
    ``task_type`` discriminates the two kinds, and ``config`` carries the
    type-specific parameters.

    Key fields:

    - **task_type** — ``offline`` (dataset × evaluators batch) or ``online``
      (always-on production-trace sampler)
    - **status** — ``active`` or ``disabled``; ``disabled`` only carries
      semantics for online tasks (stop sampling)
    - **evaluator_configs** — JSON array of registered evaluator names
    - **config** — JSON, type-specific:
      offline ``{"dataset_id", "answer_source", "threshold", "baseline_run_id",
      "regression_threshold", "tags", "agent_id"}``;
      online ``{"agent_id", "sampling_rate", "max_traces_per_cycle"}``
    - **last_scanned_at** — watermark for the online trace scanner
    - **metrics** — JSON counters accumulated by the online worker
      (``scanned`` / ``sampled`` / ``scored`` / ``errors``)
    """

    __tablename__ = "evaluation_tasks"

    task_type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskStatus.ACTIVE.value)
    evaluator_configs: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_eval_tasks_type_status", "task_type", "status", "deleted"),
        Index("idx_eval_tasks_workspace", "workspace_id", "deleted"),
    )


class EvaluationTaskScoreModel(BaseModel):
    """ORM model for target-typed scores produced by online evaluation tasks.

    Each record scores one evaluation metric against one production target
    (v1: a root trace). Granularity breakdowns (Model / Root-Agent / Tool)
    aggregate over these rows joined with ``traces`` attributes; ``agent_id``
    is denormalized from the owning session so Root-Agent rollups avoid a
    second join.

    Key fields:

    - **task_id** — the online task that produced this score
    - **target_type** — v1 always ``trace`` (``session`` / ``span`` / ``tool``
      reserved for later increments)
    - **target_id** — the scored entity's identifier (``traces.id``)
    - **session_id** — the target trace's session, for session queries
    - **status** — ``completed`` or ``error`` (error scores keep ``value=-1.0``
      and the failure reason in ``reasoning``)

    Idempotency: the unique constraint on
    ``(task_id, target_type, target_id, metric_name)`` makes rescans no-ops.
    """

    __tablename__ = "evaluation_task_scores"

    task_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="trace")
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm_judge")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskScoreStatus.COMPLETED.value)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "target_type",
            "target_id",
            "metric_name",
            name="uq_eval_task_scores_idempotency",
        ),
        Index("idx_eval_task_scores_task", "task_id"),
        Index("idx_eval_task_scores_target", "target_id"),
        Index("idx_eval_task_scores_session", "session_id"),
        Index("idx_eval_task_scores_workspace", "workspace_id", "deleted"),
    )


# ---------------------------------------------------------------------------
# Pydantic Schemas — Dataset
# ---------------------------------------------------------------------------


class EvaluationDatasetCreateSchema(PydanticBase):
    """Schema for creating a new evaluation dataset."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    metadata: dict | None = Field(None, alias="metadata_")


class EvaluationDatasetUpdateSchema(PydanticBase):
    """Schema for updating an existing evaluation dataset. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    metadata: dict | None = Field(None, alias="metadata_")


class EvaluationDatasetReadSchema(PydanticBase):
    """Schema for reading evaluation dataset data."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    name: str
    description: str | None
    workspace_id: uuid.UUID
    metadata: dict | None = Field(validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None


# ---------------------------------------------------------------------------
# Pydantic Schemas — Item
# ---------------------------------------------------------------------------


class EvaluationItemCreateSchema(PydanticBase):
    """Schema for creating evaluation dataset items."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    expected_answer: str | None = None
    generated_answer: str | None = None
    context: list[str] | None = None
    metadata: dict | None = Field(None, alias="metadata_")
    tags: list[str] | None = None


class EvaluationItemReadSchema(PydanticBase):
    """Schema for reading evaluation item data."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    query: str
    expected_answer: str | None
    generated_answer: str | None
    context: list | None
    workspace_id: uuid.UUID
    metadata: dict | None = Field(validation_alias="metadata_")
    tags: list[str] | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Pydantic Schemas — Run
# ---------------------------------------------------------------------------


class EvaluationRunCreateSchema(PydanticBase):
    """Schema for creating an evaluation run."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: uuid.UUID
    evaluators: list[str] = Field(..., min_length=1)
    answer_source: str = Field("manual", pattern="^(manual|pipeline|auto)$")
    tags: list[str] | None = None


class EvaluationRunReadSchema(PydanticBase):
    """Schema for reading evaluation run data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    task_id: uuid.UUID | None = None
    evaluator_configs: list
    status: str
    summary: dict | None = None
    started_at: datetime | None
    completed_at: datetime | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Pydantic Schemas — Score
# ---------------------------------------------------------------------------


class EvaluationScoreReadSchema(PydanticBase):
    """Schema for reading individual evaluation scores."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    item_id: uuid.UUID
    metric_name: str
    value: float
    reasoning: str | None
    source: str
    workspace_id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Pydantic Schemas — Task (7.2c)
# ---------------------------------------------------------------------------


class EvaluationTaskCreateSchema(PydanticBase):
    """Request schema for creating an evaluation task.

    Field-level validation (patterns, ranges) happens here; cross-field
    rules (dataset required for offline, ``agent_id`` + ``sampling_rate``
    required for online, evaluator resolvability) are enforced by the
    service layer so the error messages can name the offending fields.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(..., pattern="^(offline|online)$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    evaluators: list[str] = Field(..., min_length=1)
    # Offline-only fields
    dataset_id: uuid.UUID | None = None
    answer_source: str | None = Field(None, pattern="^(manual|pipeline|agent)$")
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    baseline_run_id: uuid.UUID | None = None
    regression_threshold: float | None = Field(None, gt=0.0, le=1.0)
    tags: list[str] | None = None
    # Online-only fields
    agent_id: uuid.UUID | None = None
    sampling_rate: float | None = Field(None, gt=0.0, le=1.0)
    max_traces_per_cycle: int | None = Field(None, ge=1, le=10_000)


class EvaluationTaskUpdateSchema(PydanticBase):
    """Schema for updating an evaluation task. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    status: str | None = Field(None, pattern="^(active|disabled)$")
    evaluators: list[str] | None = Field(None, min_length=1)
    dataset_id: uuid.UUID | None = None
    answer_source: str | None = Field(None, pattern="^(manual|pipeline|agent)$")
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    baseline_run_id: uuid.UUID | None = None
    regression_threshold: float | None = Field(None, gt=0.0, le=1.0)
    tags: list[str] | None = None
    agent_id: uuid.UUID | None = None
    sampling_rate: float | None = Field(None, gt=0.0, le=1.0)
    max_traces_per_cycle: int | None = Field(None, ge=1, le=10_000)


class EvaluationTaskReadSchema(PydanticBase):
    """Schema for reading evaluation task data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: str
    name: str
    description: str | None
    status: str
    evaluator_configs: list
    config: dict
    last_scanned_at: datetime | None
    metrics: dict
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class EvaluationTaskScoreReadSchema(PydanticBase):
    """Schema for reading target-typed online evaluation scores."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    session_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    metric_name: str
    value: float
    reasoning: str | None
    source: str
    status: str
    workspace_id: uuid.UUID
    created_at: datetime
