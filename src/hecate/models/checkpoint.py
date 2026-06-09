"""Checkpoint ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for checkpoints — immutable
snapshots of execution state used for recovery, replay, and debugging.

Checkpoints extend :class:`hecate.core.database.Base` directly rather than
:class:`hecate.models.base.BaseModel` because they are append-only records
with no ``updated_at`` column and no soft-delete support.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.core.database import Base


class CheckpointModel(Base):
    """ORM model for checkpoints — immutable execution state snapshots for recovery.

    Checkpoints are append-only records that capture the full state of a
    session at a given superstep. This design enables:

    - **Recovery**: resume a crashed or interrupted session from its last
      checkpoint.
    - **Time-travel debugging**: inspect or replay any historical state.

    This model extends :class:`hecate.core.database.Base` directly (not
    :class:`hecate.models.base.BaseModel`) because checkpoints are
    immutable — there is no ``updated_at`` column and no soft-delete
    (``deleted_at``) support. Once written, a checkpoint is never modified
    or removed.

    Key fields:

    - **superstep** — monotonically increasing counter within a session,
      identifying the execution step this snapshot corresponds to.
    - **node_id** — the graph node that was about to execute when the
      checkpoint was taken.
    - **channel_state** — JSONB field storing the complete channel snapshot
      at this superstep (the full state of all graph channels).
    - **pending_writes** — writes that were queued but not yet committed
      when the checkpoint was captured.
    - **metadata_** — SQLAlchemy attribute ``metadata_`` mapping to column
      ``metadata`` (see :class:`SessionModel` for the alias rationale).
    """

    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    superstep: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    channel_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pending_writes: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=datetime.now,
    )

    __table_args__ = (
        Index("idx_checkpoints_session", "session_id", "superstep"),
        Index("idx_checkpoints_workspace", "workspace_id"),
    )


class CheckpointCreateSchema(PydanticBase):
    """Schema for creating a new checkpoint."""

    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    superstep: int
    node_id: str | None = None
    channel_state: dict = Field(default_factory=dict)
    pending_writes: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    workspace_id: uuid.UUID | None = None


class CheckpointReadSchema(PydanticBase):
    """Schema for reading checkpoint data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    superstep: int
    node_id: str | None
    channel_state: dict
    pending_writes: list
    workspace_id: uuid.UUID
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime
