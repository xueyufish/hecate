"""Storage schema for the durable-execution core.

The package owns its tables under its own declarative base: an independent
host creates them from this metadata at startup and never needs the platform
alembic tree or management tables. PostgreSQL is the reference production
dialect; SQLite (file) is the development/CI dialect — every construct here
is portable (plain columns, unique constraints, single-statement conditional
updates), so both dialects run the same semantics.

Column notes:

- BackendRef identity is stored as (issuer_domain, id) pairs — ids are opaque
  strings and MUST NOT be flattened into a delimited key. The ref kind is
  implied per table (task/run) and re-derived on read.
- ``durable_action_intent`` carries the correlation columns (run, session,
  execution_id, tool_call_id) that make the platform Action explicit against
  the runtime's TOOL_CALL/TOOL_RESULT events without forcing any backend to
  fabricate Pregel events.
- ``durable_event_log`` is the transactional outbox: state writes in the same
  transaction append their envelope row here; readers resume by cursor.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Package-private base; the platform never registers these tables."""

    metadata = MetaData(naming_convention=naming_convention)


class TaskStateRow(Base):
    """Latest lifecycle record per task plus host-written replay input."""

    __tablename__ = "durable_task_state"

    task_issuer: Mapped[str] = mapped_column(String(256), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    recorded_at: Mapped[str] = mapped_column(String(64))
    writer_source: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Platform attribution (single writer: the platform's attach_workspace at
    # submit time); the standalone host leaves it NULL — workspace is a
    # platform concept, the deployment domain is the host's scope.
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    # Host-owned columns (single writer: the standalone host): run linkage for
    # event emission and restart replay, plus the submitted input payload.
    run_issuer: Mapped[str | None] = mapped_column(String(256))
    run_id: Mapped[str | None] = mapped_column(String(256))
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))


class SubmissionRow(Base):
    """One idempotency-key registration with its task/run association."""

    __tablename__ = "durable_submission"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    subject: Mapped[str] = mapped_column(String(256))
    workspace: Mapped[str] = mapped_column(String(256))
    request_digest: Mapped[str] = mapped_column(String(128))
    task_issuer: Mapped[str] = mapped_column(String(256))
    task_id: Mapped[str] = mapped_column(String(256))
    run_issuer: Mapped[str] = mapped_column(String(256))
    run_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[str] = mapped_column(String(64))


class CommandRow(Base):
    """Independent control-command receipt record."""

    __tablename__ = "durable_command"

    command_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    issuer: Mapped[str] = mapped_column(String(256))
    task_issuer: Mapped[str] = mapped_column(String(256))
    task_id: Mapped[str] = mapped_column(String(256))
    issued_at: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    # Platform attribution at record time (see TaskStateRow.workspace_id).
    workspace_id: Mapped[str | None] = mapped_column(String(64))
    run_issuer: Mapped[str | None] = mapped_column(String(256))
    run_id: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[str | None] = mapped_column(String(64))
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_schema_ref: Mapped[str | None] = mapped_column(String(256))
    detail_ns: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(64))


class ActionIntentRow(Base):
    """Intent persisted before dispatch plus claim arbitration state."""

    __tablename__ = "durable_action_intent"

    action_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    action_name: Mapped[str] = mapped_column(String(256))
    arguments_digest: Mapped[str] = mapped_column(String(128))
    side_effect_class: Mapped[str] = mapped_column(String(32))
    # Explicit correlation to the runtime TOOL_CALL/TOOL_RESULT events.
    task_issuer: Mapped[str | None] = mapped_column(String(256))
    task_id: Mapped[str | None] = mapped_column(String(256))
    run_issuer: Mapped[str | None] = mapped_column(String(256))
    run_id: Mapped[str | None] = mapped_column(String(256))
    session_id: Mapped[str | None] = mapped_column(String(256))
    execution_id: Mapped[str | None] = mapped_column(String(256))
    tool_call_id: Mapped[str | None] = mapped_column(String(256))
    # Claim slot: ``active`` marks a claim with no recorded outcome yet;
    # ``claim_token`` is the per-action fencing token (monotonic).
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    claim_token: Mapped[int] = mapped_column(Integer, default=0)
    claim_holder: Mapped[str | None] = mapped_column(String(256))
    claimed_at: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))


class ActionOutcomeRow(Base):
    """Latest outcome per action with the real result content/reference."""

    __tablename__ = "durable_action_outcome"

    action_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16))
    result_digest: Mapped[str | None] = mapped_column(String(128))
    result_ref: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result_payload: Mapped[Any | None] = mapped_column(JSON)
    recorded_at: Mapped[str] = mapped_column(String(64))


class EventRow(Base):
    """Transactional-outbox row: one governance event envelope."""

    __tablename__ = "durable_event_log"
    __table_args__ = (
        UniqueConstraint("run_issuer", "run_id", "source", "source_sequence", name="uq_event_stream_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_issuer: Mapped[str] = mapped_column(String(256))
    run_id: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(64))
    source_sequence: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[str] = mapped_column(String(64))


class RunSequenceRow(Base):
    """Per-(run, source) monotonic sequence allocator for the event log."""

    __tablename__ = "durable_run_sequence"

    run_issuer: Mapped[str] = mapped_column(String(256), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)


class LeaseRow(Base):
    """Resource-key lease with a monotonic fencing token."""

    __tablename__ = "durable_lease"

    lease_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    holder: Mapped[str] = mapped_column(String(256))
    fencing_token: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[str] = mapped_column(String(64))
    expires_at_epoch: Mapped[float] = mapped_column(Float)
    acquired_at: Mapped[str] = mapped_column(String(64))


class OutboxCursorRow(Base):
    """Relay cursor over the transactional outbox (worker-owned).

    ``last_event_row_id`` is the highest consumed ``durable_event_log.id``;
    ``skipped_event_ids`` records events abandoned after bounded consecutive
    projection failures so a poison envelope cannot block the relay forever
    while staying visible (the authoritative row remains in the log).
    """

    __tablename__ = "durable_outbox_cursor"

    relay_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_event_row_id: Mapped[int] = mapped_column(Integer, default=0)
    skipped_event_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # Bounded-retry bookkeeping: the event currently failing projection and
    # how many consecutive pumps have failed on it (across pump cycles).
    failing_event_id: Mapped[str | None] = mapped_column(String(64))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String(64))
