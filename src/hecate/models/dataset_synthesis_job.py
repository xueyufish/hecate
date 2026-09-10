"""Dataset synthesis job ORM model and Pydantic schemas.

Mirror of :class:`FineTuningJobModel` shape — same lifecycle status set,
same ``config`` / ``metrics`` JSON pattern. The synthesis pipeline uses
this job row to track long-running (>20 items) generation tasks; small
synchronous requests (≤20 items) do not write a job row.

Lifecycle:

- ``queued`` — accepted, awaiting worker pickup
- ``running`` — generation in progress
- ``completed`` — finished successfully
- ``failed`` — unrecoverable error; ``error_message`` populated
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class DatasetSynthesisJobModel(BaseModel):
    """ORM model for AI dataset synthesis jobs.

    Fields:

    - **target_dataset_id** — the dataset where synthesized items land. Nullable
      until the first item is written; populated by the pipeline.
    - **strategy** — ``"generation"`` / ``"evolution"`` / ``"adversarial"``
    - **status** — ``queued`` / ``running`` / ``completed`` / ``failed``
    - **config** — JSON: ``{"seed_dataset_id": ..., "topic": ..., "adversarial_intent": ...,
      "count": ..., "target_dataset_name": ..., "llm_config": {...}, "quality_threshold": 0.6}``
    - **metrics** — JSON: ``{"items_generated": int, "items_filtered": int, "filter_breakdown": {...}}``
    """

    __tablename__ = "dataset_synthesis_jobs"

    target_dataset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluation_datasets.id"), nullable=True)
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_dataset_synthesis_jobs_status", "status", "deleted"),
        Index("idx_dataset_synthesis_jobs_workspace", "workspace_id", "deleted"),
        Index("idx_dataset_synthesis_jobs_target_dataset", "target_dataset_id", "deleted"),
    )


class DatasetSynthesisJobCreateSchema(PydanticBase):
    """Request schema for ``POST /evaluation/datasets/synthesize``.

    The endpoint derives sync vs async from ``count`` (≤20 sync, >20 async),
    so this single schema covers both paths.
    """

    model_config = ConfigDict(extra="forbid")

    seed_dataset_id: uuid.UUID | None = None
    topic: str | None = Field(None, max_length=2000)
    strategy: str = Field(..., pattern="^(generation|evolution|adversarial)$")
    adversarial_intent: str | None = Field(
        None,
        pattern="^(prompt_injection_basic|prompt_injection_indirect|jailbreak_dan_style|pii_extraction|jailbreak_roleplay)$",
    )
    count: int = Field(..., ge=1, le=100)
    target_dataset_name: str = Field(..., min_length=1, max_length=255)
    target_dataset_description: str | None = Field(None, max_length=2000)
    llm_config: dict | None = None
    quality_threshold: float | None = Field(None, ge=0.0, le=1.0)
    workspace_id: uuid.UUID | None = None


class DatasetSynthesisJobReadSchema(PydanticBase):
    """Response schema for ``GET /evaluation/synthesis-jobs/{job_id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_dataset_id: uuid.UUID | None
    strategy: str
    status: str
    config: dict
    metrics: dict
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DatasetSynthesisSyncResponseSchema(PydanticBase):
    """Response schema for synchronous synthesis (``count <= 20``)."""

    target_dataset_id: uuid.UUID
    items_generated: int
    items_filtered: int
    duration_ms: int


class DatasetSynthesisAsyncResponseSchema(PydanticBase):
    """Response schema for asynchronous synthesis kickoff (``count > 20``)."""

    job_id: uuid.UUID
    status: str
