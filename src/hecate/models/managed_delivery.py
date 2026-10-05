"""Managed delivery ORM model - persisted dispatch intent (step6/7).

One row is the platform's dispatch intent for one enrolled host: the
task/run references the host must map to its local execution, the input
payload, and the delivery state. ``ManagedDeliveryService`` is the single
writer. The outbox property lives here: the intent row exists before the
host acts, and redelivery (state staying ``pending`` past a lost response)
is reconciled by the host's idempotent accept — never by a second
execution.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ManagedDeliveryModel(BaseModel):
    """One managed dispatch intent for one enrolled host.

    Fields:

    - **enrollment_id** — the admitted host this delivery targets.
    - **task_ref / run_ref** — the platform's own references (JSON
      BackendRefs); the host maps them to its local execution at accept.
    - **input_payload** — the persisted replay input (same shape the
      durable task row carries standalone).
    - **state** — ``pending`` (intent persisted, host has not accepted) or
      ``delivered`` (accept receipt recorded). Reconciliation is the
      redelivery of a pending row, never a second execution.
    - **accepted_refs / accepted_at** — the host's local task/run
      references at accept; a conflicting re-accept is rejected, not
      overwritten.
    """

    __tablename__ = "managed_deliveries"
    __table_args__ = (Index("ix_managed_deliveries_enrollment_state", "enrollment_id", "state"),)

    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standalone_enrollments.id"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
    task_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    run_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    delivery_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    accepted_refs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    accepted_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
