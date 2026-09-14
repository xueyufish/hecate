"""Publishing channel ORM model and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for
publishing channels (1.3.20) — the alias-indirection layer between
external surfaces and an agent's published version. A channel binds one
agent with a ``bind_mode`` of:

- ``published`` — tracks the agent's latest published version; a new
  publish is picked up by the next invocation automatically.
- ``pinned`` — locked to ``pinned_version`` until explicitly repointed;
  new publishes do not affect the channel.

v1 wires ``type`` ``api`` (channel-addressed chat completions) and ``im``
(IM session routing); ``embed`` / ``webhook`` are accepted as reserved
placeholders carrying status ``unwired``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel

CHANNEL_TYPES = ("api", "im", "embed", "webhook")
#: Types with an invocation/routing surface wired in v1.
WIRED_CHANNEL_TYPES = ("api", "im")
BIND_MODES = ("published", "pinned")
CHANNEL_STATUSES = ("active", "unwired", "disabled")


class ChannelModel(BaseModel):
    """ORM model for publishing channels (1.3.20).

    Key fields:

    - **type** — surface kind: ``api`` / ``im`` (wired) or ``embed`` /
      ``webhook`` (reserved placeholders, status ``unwired``).
    - **agent_id** — the bound agent.
    - **bind_mode** — ``published`` (track latest published version) or
      ``pinned`` (serve ``pinned_version``).
    - **pinned_version** — the locked version number when ``bind_mode``
      is ``pinned``; ``NULL`` otherwise.
    - **config** — per-type JSON. For ``im``: the IM instance routing key
      (``channel_type`` / ``provider_name``, with ``app_id`` reserved for
      future multi-instance providers).
    - **status** — ``active`` / ``unwired`` / ``disabled``.
    """

    __tablename__ = "channels"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), nullable=False)
    bind_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="published")
    pinned_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_channels_workspace", "workspace_id", "deleted"),
        Index("idx_channels_agent", "agent_id", "deleted"),
    )


# --- Pydantic Schemas ---


class ChannelCreateSchema(PydanticBase):
    """Request body for creating a publishing channel."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., pattern="^(api|im|embed|webhook)$")
    agent_id: uuid.UUID
    bind_mode: str = Field(default="published", pattern="^(published|pinned)$")
    pinned_version: int | None = Field(None, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelUpdateSchema(PydanticBase):
    """Request body for updating a channel (name / binding / config / status)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    bind_mode: str | None = Field(None, pattern="^(published|pinned)$")
    pinned_version: int | None = Field(None, ge=1)
    config: dict[str, Any] | None = None
    status: str | None = Field(None, pattern="^(active|unwired|disabled)$")


class ChannelReadSchema(PydanticBase):
    """Schema for reading a publishing channel."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    type: str
    agent_id: uuid.UUID
    bind_mode: str
    pinned_version: int | None
    config: dict[str, Any]
    status: str
    created_by: uuid.UUID | None = None
    created_at: datetime
