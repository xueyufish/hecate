"""Platform Run ORM model - one backend execution attempt of a task.

One row binds a Task to one attempt against one registered deployment:
an incrementing attempt number, the frozen identity chain in force at
creation, the backend run reference, the optional vendor session/turn
binding, the event-sequence cursor, and the platform-side state projection.
Projection and executor-owned facts are separate field groups (plan step4:
every field has exactly one authoritative writer) - the platform only ever
writes ``projection`` / ``event_cursor``; the executing side owns the real
lifecycle and the platform never rewrites it. Retries create new rows; a
run id is never reused to feign recovery.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class RunOrigin(StrEnum):
    """Where this run record came from (plan step4 field ownership)."""

    PLATFORM = "platform"
    IMPORTED_OBSERVATION = "imported_observation"


class RunModel(BaseModel):
    """One execution attempt (plan step4).

    Fields:

    - **task_id / deployment_id** — the owning task and the deployment the
      attempt ran against (fixed configuration and capability snapshot live
      on the deployment row; the run references, never copies them).
    - **attempt_no** — 1-based attempt counter per task; unique together
      with ``task_id`` so retries are new rows and concurrent retries
      cannot silently share a counter.
    - **identity_chain** — frozen ``IdentityChain`` serialization fixed at
      creation; later identity or delegation changes never rewrite it.
    - **backend_ref** — JSON execution-contract ``run`` BackendRef
      (issuer domain + backend-assigned id) identifying the actual backend
      execution.
    - **backend_session** — optional JSON reference to the vendor
      session/turn this attempt is bound to (hosted harnesses); unique per
      (issuer domain, session id) among non-null values via a partial
      unique index, so one vendor session can never silently back two runs.
    - **event_cursor** — platform-side cursor of consumed backend events;
      a cursor, never a statement about backend state.
    - **projection** — the platform's last observed backend status (JSON);
      written only by the platform, never treated as executor-owned truth.
    - **origin** — ``platform`` for runs the platform dispatched,
      ``imported_observation`` for runs imported from a local host's
      records; imported rows gain no dispatch or approval rights by
      construction (the registry offers no queueing API at all).
    - **local_source** — for imported runs: JSON with the source
      deployment's issuing domain and the host-local run identifier, so
      re-imports dedupe and provenance stays queryable.
    """

    __tablename__ = "runs"
    __table_args__ = (
        Index("uq_runs_task_attempt", "task_id", "attempt_no", unique=True),
        Index(
            "uq_runs_backend_session",
            "backend_session",
            unique=True,
            sqlite_where=text("backend_session IS NOT NULL"),
            postgresql_where=text("backend_session IS NOT NULL"),
        ),
        Index("ix_runs_task_id", "task_id"),
        Index("ix_runs_workspace_id", "workspace_id"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_chain: Mapped[dict] = mapped_column(JSON, nullable=False)
    backend_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    backend_session: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    event_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projection: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    origin: Mapped[RunOrigin] = mapped_column(
        String(32),
        nullable=False,
        default=RunOrigin.PLATFORM,
    )
    local_source: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
