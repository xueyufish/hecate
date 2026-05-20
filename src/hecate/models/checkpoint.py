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
    """ORM model for checkpoints — immutable execution state snapshots for recovery."""

    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    superstep: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    channel_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pending_writes: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=datetime.now,
    )

    __table_args__ = (Index("idx_checkpoints_session", "session_id", "superstep"),)


class CheckpointCreateSchema(PydanticBase):
    """Schema for creating a new checkpoint."""

    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    superstep: int
    node_id: str | None = None
    channel_state: dict = Field(default_factory=dict)
    pending_writes: list = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class CheckpointReadSchema(PydanticBase):
    """Schema for reading checkpoint data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    superstep: int
    node_id: str | None
    channel_state: dict
    pending_writes: list
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime
