"""Evaluation ORM models and Pydantic schemas.

Defines the persistence layer and API schemas for the evaluation system:

- **EvaluationDatasetModel** — named collection of test cases
- **EvaluationItemModel** — individual test case (query, expected answer, context)
- **EvaluationRunModel** — execution of evaluators against a dataset
- **EvaluationScoreModel** — individual score from one evaluator on one item
- **EvaluationTaskModel** — reusable evaluation task (offline batch / online sampler)
- **EvaluationTaskScoreModel** — target-typed score produced by an online task
  or a human annotation (7.4/7.4a)
- **AnnotationQueueModel** / **AnnotationQueueItemModel** — human annotation
  worklists over production traces
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint, text
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


class QueueItemStatus(enum.StrEnum):
    """Lifecycle states for an annotation queue item (7.4)."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    SKIPPED = "skipped"


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
    - **known_bad** — exemption marker: the item is known to be broken (bad
      expected answer, stale fixture, ...). Known-bad items still execute and
      still record scores, but run aggregation excludes them from pass_rate /
      consistency_rate denominators (7.3c)
    - **known_bad_reason** — required when ``known_bad`` is true; why the item
      is exempt
    - **known_bad_marked_by** / **known_bad_marked_at** — audit provenance,
      filled server-side when the mark is set
    - **known_bad_expires_at** — reserved for future expiry-based re-review;
      no logic reads it yet
    """

    __tablename__ = "evaluation_items"

    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_answer: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    context: Mapped[list | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    known_bad: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    known_bad_reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    known_bad_marked_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    known_bad_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    known_bad_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
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
    - **workflow_id** / **workflow_version** — populated when ``answer_source``
      is ``workflow`` (7.3); ``workflow_version`` is locked at run start.
    - **dataset_snapshot** — JSON snapshot of dataset items + content hash,
      captured at run start so regression comparison remains meaningful
      even if the dataset is edited between runs.
    - **repetitions** — number of times each item was executed; ``>=1``.
      When ``>1`` the run summary also exposes ``consistency_rate``.
    """

    __tablename__ = "evaluation_runs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evaluator_configs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.PENDING.value)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dataset_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    repetitions: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    trajectory: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
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
    """ORM model for target-typed scores (automated + human).

    Each record scores one evaluation metric against one production target
    (v1: a root trace). Granularity breakdowns (Model / Root-Agent / Tool)
    aggregate over these rows joined with ``traces`` attributes; ``agent_id``
    is denormalized from the owning session so Root-Agent rollups avoid a
    second join.

    Key fields:

    - **task_id** — the online task that produced this score; ``NULL`` for
      human annotation rows (7.4)
    - **target_type** — v1 always ``trace`` (``session`` / ``span`` / ``tool``
      reserved for later increments)
    - **target_id** — the scored entity's identifier (``traces.id``)
    - **session_id** — the target trace's session, for session queries
    - **status** — ``completed`` or ``error`` (error scores keep ``value=-1.0``
      and the failure reason in ``reasoning``)

    Human rows (``source="human"``) additionally carry ``annotator_id``, an
    optional ``overrides_score_id`` (pointer to the superseded automated row,
    7.4a), ``reason_code`` and ``value_label`` (categorical/boolean
    annotations store the category string here and its index in ``value``).

    Idempotency: a partial unique index on
    ``(task_id, target_type, target_id, metric_name) WHERE task_id IS NOT
    NULL`` makes automated rescans no-ops; human rows are exempt (they upsert
    per ``(annotator_id, target_id, metric_name)`` at the service layer).
    """

    __tablename__ = "evaluation_task_scores"

    task_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="trace")
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    value_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm_judge")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskScoreStatus.COMPLETED.value)
    annotator_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    overrides_score_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index(
            "uq_eval_task_scores_idempotency",
            "task_id",
            "target_type",
            "target_id",
            "metric_name",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
            sqlite_where=text("task_id IS NOT NULL"),
        ),
        Index("idx_eval_task_scores_task", "task_id"),
        Index("idx_eval_task_scores_target", "target_id"),
        Index("idx_eval_task_scores_session", "session_id"),
        Index("idx_eval_task_scores_workspace", "workspace_id", "deleted"),
        Index("idx_eval_task_scores_human", "target_id", "metric_name", "source"),
    )


class AnnotationQueueModel(BaseModel):
    """ORM model for a human annotation queue (7.4).

    A named worklist of production traces that reviewers label and score.
    ``metric_defs`` constrains the annotation form: each definition has a
    ``name`` (unique within the queue), a ``data_type`` (``numeric`` with
    ``min``/``max``, ``categorical`` with ``categories``, or ``boolean``).
    ``assigned_user_ids`` — when non-empty — restricts who may claim items.
    """

    __tablename__ = "annotation_queues"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    metric_defs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    assigned_user_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (Index("idx_annotation_queues_workspace", "workspace_id", "deleted"),)


class AnnotationQueueItemModel(BaseModel):
    """ORM model for one annotated target inside an annotation queue.

    Each item references one root trace (``target_type="trace"`` v1) and
    moves through ``pending → claimed → completed | skipped``. A trace may
    appear in a queue at most once (unique constraint). Attribution fields
    (``added_by`` / ``claimed_by`` / ``completed_by``) record who did what.
    """

    __tablename__ = "annotation_queue_items"

    queue_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, default="trace")
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=QueueItemStatus.PENDING.value)
    added_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        UniqueConstraint(
            "queue_id",
            "target_type",
            "target_id",
            name="uq_annotation_queue_items_target",
        ),
        Index("idx_annotation_queue_items_status", "queue_id", "status", "deleted"),
        Index("idx_annotation_queue_items_workspace", "workspace_id", "deleted"),
    )


class EvaluationBackflowRuleModel(BaseModel):
    """ORM model for an automated trace-backflow rule (7.2d).

    A rule selects scored production traces of one **online** evaluation task
    by score-band filters and materializes them into one evaluation dataset
    when explicitly triggered (no cron, no worker hook). Filters combine with
    AND semantics: a trace qualifies when every filter matches its latest
    score for that metric. ``metadata_`` carries a ``last_run`` summary
    (``{at, created, skipped}``) for observability.

    Materialized items are attributed via ``metadata_.backflow.trace_id`` and
    dedupe against the human-annotation path (``metadata_.annotation.trace_id``)
    — see :mod:`hecate.ops.evaluation.trace_dedup`.
    """

    __tablename__ = "evaluation_backflow_rules"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    filters: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=500, server_default="500")
    max_turns: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, server_default="{}")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (Index("idx_eval_backflow_rules_workspace", "workspace_id", "deleted"),)


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
    """Schema for creating evaluation dataset items.

    Known-bad marker fields are intentionally absent: items are created
    unmarked, and exemption is applied afterwards through the dedicated
    item update API (7.3c) so provenance is always server-recorded.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    expected_answer: str | None = None
    generated_answer: str | None = None
    context: list[str] | None = None
    metadata: dict | None = Field(None, alias="metadata_")
    tags: list[str] | None = None


class EvaluationItemUpdateSchema(PydanticBase):
    """Schema for updating the known-bad exemption state of an item (7.3c).

    ``known_bad`` is required: the update API is the marking path, not a
    generic item-content editor. ``known_bad_marked_by`` and
    ``known_bad_marked_at`` are server-managed provenance and cannot be
    set by clients.
    """

    model_config = ConfigDict(extra="forbid")

    known_bad: bool
    known_bad_reason: str | None = None
    known_bad_expires_at: datetime | None = None


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
    known_bad: bool = False
    known_bad_reason: str | None = None
    known_bad_marked_by: uuid.UUID | None = None
    known_bad_marked_at: datetime | None = None
    known_bad_expires_at: datetime | None = None
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
    workflow_id: uuid.UUID | None = None
    workflow_version: int | None = None
    dataset_snapshot: dict | None = None
    repetitions: int | None = None
    trajectory: list | None = None
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
    answer_source: str | None = Field(None, pattern="^(manual|pipeline|agent|workflow)$")
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    baseline_run_id: uuid.UUID | None = None
    regression_threshold: float | None = Field(None, gt=0.0, le=1.0)
    tags: list[str] | None = None
    # Workflow answer source fields (7.3)
    workflow_id: uuid.UUID | None = None
    workflow_version: int | None = Field(None, ge=1)
    repetitions: int | None = Field(None, ge=1, le=100)
    max_total_executions: int | None = Field(None, ge=1, le=1_000_000)
    max_in_flight: int | None = Field(None, ge=1, le=64)
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
    answer_source: str | None = Field(None, pattern="^(manual|pipeline|agent|workflow)$")
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    baseline_run_id: uuid.UUID | None = None
    regression_threshold: float | None = Field(None, gt=0.0, le=1.0)
    tags: list[str] | None = None
    workflow_id: uuid.UUID | None = None
    workflow_version: int | None = Field(None, ge=1)
    repetitions: int | None = Field(None, ge=1, le=100)
    max_total_executions: int | None = Field(None, ge=1, le=1_000_000)
    max_in_flight: int | None = Field(None, ge=1, le=64)
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
    """Schema for reading target-typed scores (automated + human)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID | None = None
    target_type: str
    target_id: uuid.UUID
    session_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    metric_name: str
    value: float
    value_label: str | None = None
    reasoning: str | None
    source: str
    status: str
    annotator_id: uuid.UUID | None = None
    overrides_score_id: uuid.UUID | None = None
    reason_code: str | None = None
    workspace_id: uuid.UUID
    created_at: datetime


# ---------------------------------------------------------------------------
# Pydantic Schemas — Annotation queues (7.4)
# ---------------------------------------------------------------------------


class AnnotationMetricDefSchema(PydanticBase):
    """One annotation metric definition constraining the annotation form."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    data_type: str = Field(..., pattern="^(numeric|categorical|boolean)$")
    min: float | None = Field(None, ge=-1_000_000.0, le=1_000_000.0)
    max: float | None = Field(None, ge=-1_000_000.0, le=1_000_000.0)
    categories: list[str] | None = Field(None, min_length=1, max_length=50)


class AnnotationQueueCreateSchema(PydanticBase):
    """Request schema for creating an annotation queue."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=5000)
    metric_defs: list[AnnotationMetricDefSchema] = Field(..., min_length=1)
    assigned_user_ids: list[uuid.UUID] | None = None


class AnnotationQueueUpdateSchema(PydanticBase):
    """Schema for updating an annotation queue. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=5000)
    metric_defs: list[AnnotationMetricDefSchema] | None = Field(None, min_length=1)
    assigned_user_ids: list[uuid.UUID] | None = None


class AnnotationQueueReadSchema(PydanticBase):
    """Schema for reading annotation queue data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    instructions: str | None
    metric_defs: list
    assigned_user_ids: list
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AnnotationQueueItemReadSchema(PydanticBase):
    """Schema for reading an annotation queue item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    queue_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    status: str
    added_by: uuid.UUID | None
    claimed_by: uuid.UUID | None
    claimed_at: datetime | None
    completed_by: uuid.UUID | None
    completed_at: datetime | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Pydantic Schemas — Backflow rules (7.2d)
# ---------------------------------------------------------------------------


class BackflowScoreFilterSchema(PydanticBase):
    """One score-band filter; a trace matches when its latest score for the
    metric falls inside the (inclusive) band. Multiple filters combine with
    AND semantics at the service layer."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(..., min_length=1, max_length=100)
    min_score: float | None = Field(None, ge=-1.0, le=1.0)
    max_score: float | None = Field(None, ge=-1.0, le=1.0)


class EvaluationBackflowRuleCreateSchema(PydanticBase):
    """Request schema for creating a backflow rule.

    Cross-field rules (score band order, referenced task is online, dataset
    exists, workspace-unique name) are enforced by the service layer so the
    error messages can name the offending fields.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    task_id: uuid.UUID
    dataset_id: uuid.UUID
    filters: list[BackflowScoreFilterSchema] = Field(..., min_length=1)
    limit: int = Field(500, ge=1, le=10_000)
    max_turns: int | None = Field(None, ge=1, le=10_000)


class EvaluationBackflowRuleUpdateSchema(PydanticBase):
    """Schema for updating a backflow rule. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    task_id: uuid.UUID | None = None
    dataset_id: uuid.UUID | None = None
    filters: list[BackflowScoreFilterSchema] | None = Field(None, min_length=1)
    limit: int | None = Field(None, ge=1, le=10_000)
    max_turns: int | None = Field(None, ge=1, le=10_000)


class EvaluationBackflowRuleReadSchema(PydanticBase):
    """Schema for reading backflow rule data."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    name: str
    task_id: uuid.UUID
    dataset_id: uuid.UUID
    filters: list
    limit: int
    max_turns: int | None
    workspace_id: uuid.UUID
    metadata: dict | None = Field(validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime
