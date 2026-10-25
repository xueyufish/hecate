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
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
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


class BudgetScopeRow(Base):
    """Durable mutex row for all reservations in the local budget scope."""

    __tablename__ = "durable_budget_scope"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class BudgetReservationRow(Base):
    """Per-invocation budget reservation on the standalone host (step7).

    One open reservation per ``invocation_key`` (partial unique index over
    unreleased/unsettled rows); settlement records actual usage, unknown
    outcomes hold until reconciliation.
    """

    __tablename__ = "durable_budget_reservations"
    __table_args__ = (
        Index(
            "uq_durable_budget_open",
            "invocation_key",
            unique=True,
            sqlite_where=text("released = 0 AND settled = 0"),
            postgresql_where=text("released = false AND settled = false"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invocation_key: Mapped[str] = mapped_column(String(512), index=True)
    units: Mapped[float] = mapped_column(Float)
    released: Mapped[bool] = mapped_column(Boolean, default=False)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    unknown_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    settled_units: Mapped[float | None] = mapped_column(Float)
    usage_status: Mapped[str | None] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[str] = mapped_column(String(64))


class LocalApprovalRow(Base):
    """Local approval authority record on the standalone host (step7 5.4).

    One row per ``approval_ref``; judgment and consumption are separate
    fields; one-time consumption claims the row by
    ``consumed_by_invocation_id``. Signed only by locally registered
    approvers; the record survives restarts.
    """

    __tablename__ = "durable_local_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    approval_ref: Mapped[str] = mapped_column(String(512), unique=True)
    principal_fingerprint: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(512))
    arguments_digest: Mapped[str] = mapped_column(String(128))
    target_resources: Mapped[list[str] | None] = mapped_column(JSON)
    issued_at: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[str | None] = mapped_column(String(64))
    one_time: Mapped[bool] = mapped_column(Boolean, default=True)
    judgment: Mapped[str] = mapped_column(String(20), default="approved")
    decided_by: Mapped[str] = mapped_column(String(256))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    consumed_by_invocation_id: Mapped[str | None] = mapped_column(String(512))
    domains: Mapped[list[str] | None] = mapped_column(JSON)
    task_ref: Mapped[str | None] = mapped_column(String(256))
    deployment_id: Mapped[str | None] = mapped_column(String(256))
    reason: Mapped[str | None] = mapped_column(Text)
    anchor_wall: Mapped[str | None] = mapped_column(String(64))
    anchor_monotonic: Mapped[float | None] = mapped_column(Float)
    clock_epoch: Mapped[str | None] = mapped_column(String(64))


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


class OutboxReceiptRow(Base):
    """Per-relay delivery receipt; allocation order is not commit order."""

    __tablename__ = "durable_outbox_receipt"

    relay_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String(64))


class AcceptedDecisionRow(Base):
    """One accepted Agent decision with its full, restorable action set.

    Step6 reliable-commit semantics: the decision and its actions commit in
    one transaction (single row here; actions live in the ``actions`` JSON
    payload with real parameters, never digests alone). Unique per
    (issuer_domain, run_id, decision_step_id); the normalized content digest
    arbitrates idempotent re-submission versus explicit conflict. Fence
    columns carry the submitter's monotonic fencing context; an expired
    token is refused at commit time.
    """

    __tablename__ = "durable_accepted_decision"

    __table_args__ = (UniqueConstraint("issuer_domain", "run_id", "decision_step_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issuer_domain: Mapped[str] = mapped_column(String(256))
    run_id: Mapped[str] = mapped_column(String(256))
    decision_step_id: Mapped[str] = mapped_column(String(256))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    input_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    accepted_output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_digest: Mapped[str] = mapped_column(String(128))
    fence_holder: Mapped[str | None] = mapped_column(String(256))
    fence_token: Mapped[int] = mapped_column(Integer, default=0)
    accepted_at: Mapped[str] = mapped_column(String(64))


class InputRevisionRow(Base):
    """One accepted human input revision with its causal turn boundary.

    Controller-loop input history: immutable once written (old inputs are
    never overwritten by the newest snapshot). ``effective_turn`` is one
    past the committed decision-chain length at acceptance, so replay
    rebuilds the true causal order — earlier decisions and tool results,
    then the inputs accepted at that point, then later decisions. Unique
    per (task, source command) for idempotent wake replays; the content
    digest detects corruption on read instead of guessing history.
    """

    __tablename__ = "durable_input_revision"

    __table_args__ = (UniqueConstraint("task_issuer", "task_id", "source_ref"),)

    revision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_issuer: Mapped[str] = mapped_column(String(256))
    task_id: Mapped[str] = mapped_column(String(256))
    run_issuer: Mapped[str] = mapped_column(String(256))
    run_id: Mapped[str] = mapped_column(String(256))
    seq: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(256))
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_sha256: Mapped[str] = mapped_column(String(128))
    wait_token: Mapped[str | None] = mapped_column(String(256))
    effective_turn: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(64))
