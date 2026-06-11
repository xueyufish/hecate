"""Evaluation ORM models and Pydantic schemas.

Defines the persistence layer and API schemas for the evaluation system:

- **EvaluationDatasetModel** — named collection of test cases
- **EvaluationItemModel** — individual test case (query, expected answer, context)
- **EvaluationRunModel** — execution of evaluators against a dataset
- **EvaluationScoreModel** — individual score from one evaluator on one item
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class RunStatus(enum.StrEnum):
    """Lifecycle states for an evaluation run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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
    - **evaluator_configs** — JSON array of evaluator names + their configs
    - **status** — ``pending``, ``running``, ``completed``, or ``failed``
    - **started_at** / **completed_at** — timing markers for the run
    """

    __tablename__ = "evaluation_runs"

    dataset_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    evaluator_configs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.PENDING.value)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_eval_runs_dataset", "dataset_id"),
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


class EvaluationRunReadSchema(PydanticBase):
    """Schema for reading evaluation run data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    evaluator_configs: list
    status: str
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
