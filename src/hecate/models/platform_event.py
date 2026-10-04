"""Platform governance-event and run-event store model.

One row per persisted ``EventEnvelope`` (contract
``contracts/execution/events.py``): governance events (task lifecycle
transitions, command receipts, reconciliation marks — actor and source
required under the governance profile) and the run event stream mapped
from engine executions dispatched through the task control plane. The
full envelope serialization is preserved verbatim; the ref/sequence
columns exist for cursor-paginated reads by run reference. Owned by the
execution domain (single writer: the governance-event service); the
runtime's own EventStore semantics are untouched (plan step6 keeps them
separate).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class PlatformEventModel(BaseModel):
    """One persisted event envelope on a run's platform event stream.

    Fields:

    - **event_id** — envelope event id; globally unique.
    - **kind** — ``event`` or ``gap`` (explicit gap markers survive).
    - **ref columns** — task/run ``BackendRef`` split into issuer domain
      + id for indexed lookup; the envelope keeps the full refs.
    - **source_sequence** — per-run monotonically increasing sequence as
      emitted by the writer; cursor reads resume from it. Readers never
      rebuild a global order from it.
    - **actor / source** — governance profile fields; nullable at the
      column level because run-stream envelopes may omit them, but the
      governance emitter validates them non-empty before persisting.
    - **envelope** — the complete envelope serialization (round-trips
      through ``EventEnvelope.to_dict``/``from_dict``).
    """

    __tablename__ = "platform_events"
    __table_args__ = (
        Index("uq_platform_events_event_id", "event_id", unique=True),
        Index(
            "ix_platform_events_run_sequence",
            "workspace_id",
            "run_issuer_domain",
            "run_ref_id",
            "source_sequence",
        ),
        Index("ix_platform_events_task_ref", "task_issuer_domain", "task_ref_id"),
    )

    event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    task_issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    task_ref_id: Mapped[str] = mapped_column(String(512), nullable=False)
    run_issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    run_ref_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[str] = mapped_column(Text, nullable=False)
    payload_schema_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    actor_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    envelope: Mapped[dict] = mapped_column(JSON, nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
