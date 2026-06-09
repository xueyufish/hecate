"""Budget Snapshot ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for budget snapshots — records of
token budget usage per session for Context Engineering budget governance.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class BudgetSnapshotModel(BaseModel):
    """ORM model for budget snapshots — token budget usage tracking.

    Key fields:

    - **session_id** — the session this budget snapshot belongs to.
    - **total_budget** — total token budget allocated for the session.
    - **tokens_used** — cumulative tokens used so far.
    - **tokens_remaining** — remaining tokens in the budget.
    - **degradation_level** — the degradation level applied (if any):
      "none", "drop", "compress", or "emergency".
    """

    __tablename__ = "budget_snapshots"

    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    total_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    degradation_level: Mapped[str] = mapped_column(String(20), default="none")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_budget_snapshots_session", "session_id"),
        Index("idx_budget_snapshots_workspace", "workspace_id", "deleted"),
    )


class BudgetSnapshotCreateSchema(PydanticBase):
    """Schema for creating a new budget snapshot."""

    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    total_budget: int = Field(..., gt=0)
    tokens_used: int = Field(default=0, ge=0)
    tokens_remaining: int = Field(..., ge=0)
    degradation_level: str = Field(default="none", pattern="^(none|drop|compress|emergency)$")
    workspace_id: uuid.UUID | None = None


class BudgetSnapshotReadSchema(PydanticBase):
    """Schema for reading budget snapshot data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    total_budget: int
    tokens_used: int
    tokens_remaining: int
    degradation_level: str
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
