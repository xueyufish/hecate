"""Platform task lifecycle projection and submission idempotency models.

Two step6 tables owned by the execution domain (single writer: the
platform durable adapters in ``hecate.execution.platform_durable``):

- ``task_lifecycle_states`` — the platform-side lifecycle projection for
  one task reference (contract ``TaskStateRecord`` shape). Kept separate
  from the ``tasks`` responsibility rows: the contract addresses state by
  ``BackendRef`` (issuer domain + id), and the responsibility record and
  the lifecycle projection are separate field groups with separate
  writers.
- ``task_submissions`` — one row per idempotency key, binding the
  server-verified subject and workspace to the canonical request digest
  and the Task/Run association minted for that submission.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class TaskLifecycleStateModel(BaseModel):
    """Durable lifecycle projection for one task reference (plan step6).

    Fields:

    - **issuer_domain / task_ref_id** — the contract task ``BackendRef``
      this state belongs to; unique together. The ref id is the platform
      task UUID for platform-issued tasks but is stored as a string so
      foreign-issued refs remain addressable.
    - **lifecycle_state** — one ``TaskLifecycleState`` value (closed
      enum; contract ``contracts/execution/durable.py``).
    - **revision** — optimistic concurrency counter; every transition
      increments it, and stale expected revisions are rejected.
    - **recorded_at** — contract timestamp string (ISO-8601 UTC).
    - **writer_source** — which writer produced the record (e.g.
      ``platform``), kept for provenance.
    - **workspace_id** — owning workspace for scoped queries; nullable
      because foreign-issued refs may predate platform attribution.
    """

    __tablename__ = "task_lifecycle_states"
    __table_args__ = (
        Index("uq_task_lifecycle_ref", "issuer_domain", "task_ref_id", unique=True),
        Index("ix_task_lifecycle_workspace_state", "workspace_id", "lifecycle_state"),
    )

    issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    task_ref_id: Mapped[str] = mapped_column(String(512), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False)
    writer_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class TaskSubmissionModel(BaseModel):
    """One idempotent submission association (plan step6).

    Fields:

    - **idempotency_key** — caller-supplied key; globally unique. Replays
      with the same digest return the stored association; a different
      digest is a conflict and never overwrites the row.
    - **subject / workspace** — the server-verified scope the key is
      bound to; a replay under a different scope is rejected.
    - **request_digest** — canonical-JSON sha256 of the request body.
    - **task/run ref columns** — the association minted for the first
      accepted submission with this key.
    """

    __tablename__ = "task_submissions"
    __table_args__ = (
        Index("uq_task_submissions_key", "idempotency_key", unique=True),
        Index("ix_task_submissions_task_ref", "task_issuer_domain", "task_ref_id"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    task_issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    task_ref_id: Mapped[str] = mapped_column(String(512), nullable=False)
    run_issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    run_ref_id: Mapped[str] = mapped_column(String(512), nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
