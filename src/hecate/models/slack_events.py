"""ORM model for the Slack event-observation ledger.

Observed events retain their verified identifiers. Signed refusals
are counted by reason and time bucket without recording payload fields.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class SlackEventObservationModel(BaseModel):
    """One observed event or a bounded counter for signed refusals.

    Attributes:

    - **source** — the observation source slug (always ``"slack"`` for
      now; part of the uniqueness so future sources cannot collide).
    - **app_id / team_id / channel_id** — the verified source identity
      this profile binds; identifiers only.
    - **event_id** — Slack's event ID for an observed event, or an internal
      time-bucket key for a refusal counter.
    - **event_type** — the observed Slack type, empty for refusal counters.
    - **workspace_id** — the server-configured Hecate workspace binding,
      recorded for the future triggering phase's audit trail.
    - **disposition** — observed or a fixed refusal code.
    - **delivery_count** — one for an observed event, or a capped refusal count.
    - **received_at** — when the event or latest counted refusal was received.
    """

    __tablename__ = "slack_event_observations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source", "event_id", name="uq_slack_event_observations_workspace_event"),
        Index("ix_slack_event_observations_created_at", "created_at"),
    )

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="slack")
    app_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    team_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    disposition: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def to_public_dict(self) -> dict[str, Any]:
        """The read-only ops projection — identifiers and disposition only."""
        return {
            "id": str(self.id),
            "source": self.source,
            "app_id": self.app_id,
            "team_id": self.team_id,
            "channel_id": self.channel_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "workspace_id": str(self.workspace_id),
            "disposition": self.disposition,
            "delivery_count": self.delivery_count,
            "received_at": self.received_at.isoformat() if self.received_at else None,
        }
