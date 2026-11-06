"""SQL reference storage implementing the three durable-execution seams.

``SqlDurableStore`` is one class implementing :class:`DurableTaskStore`,
:class:`ControlCommandRecorder`, and :class:`ActionLedger` over one engine so
the contract suite registers it as ``(store, store, store)`` and every state
write can append its governance-event row in the same transaction.

Portability rules (PostgreSQL reference dialect, SQLite development dialect):

- atomicity comes from single-statement conditional ``UPDATE`` (claim,
  sequence allocation) plus unique-constraint INSERT with a savepoint-guarded
  first-creator path — never from dialect-specific locks;
- every method call opens its own short-lived session; sessions are never
  shared across concurrent runs;
- recovery semantics mirror ``InMemoryActionLedger`` point for point; the
  parameterized contract suite pins that equivalence. A failed recovery read
  returns ``store_unavailable`` — never a downgrade to ``never_started``.

Beyond the seams (the host consumes these; the seams stay minimal):

- :meth:`submit_task` — idempotent submission + task creation + run linkage +
  replay input + ``task_submitted`` event, one transaction;
- :meth:`claim_ex` / :meth:`record_outcome_ex` — claim tokens (per-action
  fencing) and real result content; a late outcome under a stale token is
  rejected and only journaled as an event;
- :meth:`list_tasks` / :meth:`list_run_actions` / :meth:`read_events` — the
  minimal reconciliation/event-query API the host exposes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, func, inspect, select, tuple_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hecate_durable.contracts.durable import (
    LIVE_INPUT_REVISION_SOURCE,
    ActionIntent,
    ActionLedgerState,
    ActionOutcome,
    ActionOutcomeRecord,
    ActionRecovery,
    ClaimReceipt,
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
    ToolSideEffectClass,
    may_auto_replay,
    validate_command_transition,
    validate_task_transition,
)
from hecate_durable.contracts.references import BackendRef, RefKind, require_kind
from hecate_durable.seams import ActionLedger, ControlCommandRecorder, DurableTaskStore
from hecate_durable.storage.eventlog import EventPage, SqlEventLog
from hecate_durable.storage.lease import LeaseHandle, LeaseManager, task_dispatch_lease_key
from hecate_durable.storage.models import (
    ActionIntentRow,
    ActionOutcomeRow,
    Base,
    CommandRow,
    SubmissionRow,
    TaskStateRow,
)

logger = logging.getLogger(__name__)

_SQLITE_MEMORY_URLS = ("sqlite://", "sqlite:///:memory:")
_EPOCH_SENTINEL = "1970-01-01T00:00:00Z"
# Sentinel for the wake path: derive the input boundary from the committed
# decision count under the task row lock. An explicit ``None`` in the
# revision dict instead persists an unbound (pending) live supplement.
_COMPUTE_INPUT_BOUNDARY = object()

# Why a terminal task settles its unconsumed live supplements as not applied.
_LIVE_SETTLEMENT_REASONS = {
    TaskLifecycleState.SUCCEEDED: "task_succeeded",
    TaskLifecycleState.FAILED: "task_failed",
    TaskLifecycleState.CANCELLED: "task_cancelled",
}


class LateOutcomeError(Exception):
    """An outcome arrived under a stale claim token (fencing rejection)."""

    def __init__(self, action_key: str, presented_token: int) -> None:
        super().__init__(
            f"action {action_key!r}: outcome presented with stale claim token {presented_token}; "
            "the authoritative ledger state is unchanged and the late receipt was journaled"
        )
        self.action_key = action_key
        self.presented_token = presented_token


class _FencedRejectionError(Exception):
    """Internal control-flow signal: the fenced outcome write lost the CAS."""


def _default_clock() -> str:
    return datetime.now(UTC).isoformat()


def _task_ref(row_issuer: str, row_id: str) -> BackendRef:
    return BackendRef(kind=RefKind.TASK, issuer_domain=row_issuer, id=row_id)


def _run_ref(row_issuer: str, row_id: str) -> BackendRef:
    return BackendRef(kind=RefKind.RUN, issuer_domain=row_issuer, id=row_id)


def _intent_of(row: ActionIntentRow) -> ActionIntent:
    return ActionIntent(
        action_key=row.action_key,
        action_name=row.action_name,
        arguments_digest=row.arguments_digest,
        side_effect_class=ToolSideEffectClass(row.side_effect_class),
    )


def _outcome_of(action_key: str, row: ActionOutcomeRow) -> ActionOutcomeRecord:
    return ActionOutcomeRecord(
        action_key=action_key,
        outcome=ActionOutcome(row.outcome),
        result_ref=BackendRef.from_dict(row.result_ref) if row.result_ref else None,
        result_digest=row.result_digest,
    )


def _association_of(row: SubmissionRow) -> SubmissionAssociation:
    return SubmissionAssociation(
        key=IdempotencyKey(
            key=row.key,
            subject=row.subject,
            workspace=row.workspace,
            request_digest=row.request_digest,
        ),
        task_ref=_task_ref(row.task_issuer, row.task_id),
        run_ref=_run_ref(row.run_issuer, row.run_id),
    )


def _task_record_of(row: TaskStateRow) -> TaskStateRecord:
    extra = dict(row.extra or {})
    if row.workspace_id is not None:
        extra.setdefault("workspace_id", row.workspace_id)
    return TaskStateRecord(
        task_ref=_task_ref(row.task_issuer, row.task_id),
        lifecycle_state=TaskLifecycleState(row.lifecycle_state),
        revision=row.revision,
        recorded_at=row.recorded_at or _EPOCH_SENTINEL,
        writer_source=row.writer_source,
        extra=extra,
    )


def _command_of(row: CommandRow) -> ControlCommandRecord:
    run_ref = _run_ref(row.run_issuer, row.run_id) if row.run_issuer and row.run_id else None
    return ControlCommandRecord(
        command_id=row.command_id,
        kind=ControlCommandKind(row.kind),
        issuer=row.issuer,
        task_ref=_task_ref(row.task_issuer, row.task_id),
        issued_at=row.issued_at,
        state=CommandState(row.state),
        run_ref=run_ref,
        expires_at=row.expires_at,
        expected_revision=row.expected_revision,
        payload=dict(row.payload or {}),
        payload_schema_ref=row.payload_schema_ref,
        detail_ns=dict(row.detail_ns or {}),
        extra=dict(row.extra or {}),
    )


def _intent_linkage(row: ActionIntentRow) -> tuple[BackendRef | None, BackendRef | None]:
    task_ref = _task_ref(row.task_issuer, row.task_id) if row.task_issuer and row.task_id else None
    run_ref = _run_ref(row.run_issuer, row.run_id) if row.run_issuer and row.run_id else None
    return task_ref, run_ref


class SqlDurableStore(DurableTaskStore, ControlCommandRecorder, ActionLedger):
    """One engine, three seams, transactional outbox emission."""

    def __init__(
        self,
        url: str,
        *,
        clock: Callable[[], str] | None = None,
        source: str = "standalone_host",
        actor_id: str | None = None,
        lease_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or _default_clock
        self.engine = self._build_engine(url)
        self._session_factory = sessionmaker(self.engine, expire_on_commit=False, future=True)
        # Read-only lookups run on an autocommit engine WITHOUT the
        # BEGIN IMMEDIATE hook. Every store transaction currently takes the
        # WAL writer lock up front; reads that queue behind a long-held
        # writer (a dispatch loop's commit window) turn the single writer
        # slot into a convoy that exceeds the busy timeout under parallel
        # load. Reads never need that lock, so they take none at all.
        # In-memory databases share one StaticPool connection, so they must
        # keep using the write engine.
        if url.startswith("sqlite") and url not in _SQLITE_MEMORY_URLS:
            self._read_engine = self._build_engine(url, begin_immediate=False)
            self._read_session_factory = sessionmaker(self._read_engine, expire_on_commit=False, future=True)
        else:
            self._read_engine = self.engine
            self._read_session_factory = self._session_factory
        self.events = SqlEventLog(
            self._session_factory,
            read_session_factory=self._read_session_factory,
            source=source,
            actor_id=actor_id,
            clock=self._clock,
        )
        self.leases = LeaseManager(self._session_factory, clock=lease_clock)

    @staticmethod
    def _build_engine(url: str, *, begin_immediate: bool = True):
        if url in _SQLITE_MEMORY_URLS:
            engine = create_engine(
                url,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 30},
                poolclass=StaticPool,
            )
        elif url.startswith("sqlite"):
            engine = create_engine(
                url,
                future=True,
                connect_args={"check_same_thread": False, "timeout": 30},
            )
        else:
            return create_engine(url, future=True, pool_pre_ping=True)
        # SQLite deferred transactions deadlock under multi-thread write
        # contention (read-lock upgrade returns SQLITE_BUSY immediately).
        # Switching the DBAPI to autocommit and issuing BEGIN IMMEDIATE
        # ourselves takes the write lock up front so contenders wait on the
        # busy timeout instead of erroring — mirroring PostgreSQL row-lock
        # behavior for the claim/lease/sequence CAS paths.
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _sqlite_autocommit(dbapi_connection, _record):  # noqa: ANN001
            dbapi_connection.isolation_level = None

        if not begin_immediate:
            return engine

        @event.listens_for(engine, "begin")
        def _sqlite_begin_immediate(connection):  # noqa: ANN001
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        return engine

    # -- schema / lifecycle ---------------------------------------------------

    def create_schema(self) -> None:
        """Create this package's tables (standalone-host startup path)."""

        with self.engine.begin() as connection:
            Base.metadata.create_all(connection)
            columns = inspect(connection).get_columns("durable_input_revision")
            if not any(column["name"] == "settled_reason" for column in columns):
                # Existing standalone databases bypass platform Alembic. The
                # standalone host writes only bound revisions, so it needs
                # the additive column but can retain its NOT NULL constraint.
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql(
                        "ALTER TABLE durable_input_revision ADD COLUMN IF NOT EXISTS settled_reason VARCHAR(128)"
                    )
                else:
                    connection.exec_driver_sql(
                        "ALTER TABLE durable_input_revision ADD COLUMN settled_reason VARCHAR(128)"
                    )

    def dispose(self) -> None:
        self.engine.dispose()
        if self._read_engine is not self.engine:
            self._read_engine.dispose()

    def _session(self) -> Session:
        return self._session_factory()

    def _read_session(self) -> Session:
        """A session on the autocommit engine: reads take no writer lock."""

        return self._read_session_factory()

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """The store's session factory (worker/relay run their own sessions)."""

        return self._session_factory

    # -- DurableTaskStore -----------------------------------------------------

    def record_submission(
        self, key: IdempotencyKey, task_ref: BackendRef, run_ref: BackendRef
    ) -> SubmissionAssociation:
        for attempt in range(3):
            try:
                with self._session() as session, session.begin():
                    existing = session.get(SubmissionRow, key.key)
                    if existing is not None:
                        self._check_submission_digest(key, existing)
                        return _association_of(existing)
                    session.add(
                        SubmissionRow(
                            key=key.key,
                            subject=key.subject,
                            workspace=key.workspace,
                            request_digest=key.request_digest,
                            task_issuer=task_ref.issuer_domain,
                            task_id=task_ref.id,
                            run_issuer=run_ref.issuer_domain,
                            run_id=run_ref.id,
                            created_at=self._clock(),
                        )
                    )
                    session.flush()
                return SubmissionAssociation(key=key, task_ref=task_ref, run_ref=run_ref)
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    @staticmethod
    def _check_submission_digest(key: IdempotencyKey, existing: SubmissionRow) -> None:
        if (
            existing.request_digest != key.request_digest
            or existing.subject != key.subject
            or existing.workspace != key.workspace
        ):
            raise IdempotencyConflictError(key.key, existing.request_digest)

    def submit_task(
        self,
        *,
        key: IdempotencyKey,
        task_ref: BackendRef,
        run_ref: BackendRef,
        input_payload: dict[str, Any],
        dispatch_lease: LeaseHandle | None = None,
        dispatch_parent_ref: BackendRef | None = None,
        dispatch_step_index: int | None = None,
    ) -> SubmissionAssociation:
        """Idempotent submission that also creates the task and its linkage.

        Same key + same digest returns the original association without
        creating anything twice; the task row (``queued``, revision 0), run
        linkage, replay input, and the ``task_submitted`` governance event all
        commit in one transaction.

        ``dispatch_lease`` + ``dispatch_parent_ref`` + ``dispatch_step_index``
        fence the CREATION (never an idempotent replay) on the parent's live
        dispatch lease and step state inside that same transaction: a stale
        executor whose lease was taken over, or whose workflow step was
        closed, is refused instead of minting a child nobody will drive.
        """

        if (dispatch_lease is None) != (dispatch_parent_ref is None) or (dispatch_parent_ref is None) != (
            dispatch_step_index is None
        ):
            raise ValueError("dispatch fencing needs the lease, the parent ref and the step index together")

        for attempt in range(3):
            try:
                with self._session() as session, session.begin():
                    existing = session.get(SubmissionRow, key.key)
                    if existing is not None:
                        self._check_submission_digest(key, existing)
                        return _association_of(existing)
                    if dispatch_lease is not None:
                        self._assert_step_dispatchable(
                            session, dispatch_parent_ref, dispatch_step_index, dispatch_lease
                        )
                    task_row = session.execute(
                        select(TaskStateRow).where(
                            TaskStateRow.task_issuer == task_ref.issuer_domain,
                            TaskStateRow.task_id == task_ref.id,
                        )
                    ).scalar_one_or_none()
                    now = self._clock()
                    if task_row is None:
                        session.add(
                            TaskStateRow(
                                task_issuer=task_ref.issuer_domain,
                                task_id=task_ref.id,
                                lifecycle_state=TaskLifecycleState.QUEUED.value,
                                revision=0,
                                recorded_at=now,
                                writer_source="submit",
                                workspace_id=key.workspace,
                                run_issuer=run_ref.issuer_domain,
                                run_id=run_ref.id,
                                input_payload=input_payload,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    session.add(
                        SubmissionRow(
                            key=key.key,
                            subject=key.subject,
                            workspace=key.workspace,
                            request_digest=key.request_digest,
                            task_issuer=task_ref.issuer_domain,
                            task_id=task_ref.id,
                            run_issuer=run_ref.issuer_domain,
                            run_id=run_ref.id,
                            created_at=now,
                        )
                    )
                    session.flush()
                    self.events.emit(
                        session,
                        task_ref=task_ref,
                        run_ref=run_ref,
                        event_type="task_submitted",
                        payload={"subject": key.subject, "workspace": key.workspace},
                        correlation_id=key.key,
                    )
                return SubmissionAssociation(key=key, task_ref=task_ref, run_ref=run_ref)
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def _assert_step_dispatchable(
        self, session: Any, parent_ref: BackendRef | None, step_index: int | None, lease: LeaseHandle
    ) -> None:
        """Fence one workflow step's start on the parent's live dispatch lease.

        Runs inside the caller's transaction: the lease row lock serializes
        against every takeover, and the parent row lock serializes against
        the reclaim sweep's close marker, so the decision (lease valid, the
        lease binds THIS parent, the parent still running, the step still
        open) and the first write that depends on it commit or fail together.
        """

        from hecate_durable.storage.lease import task_dispatch_lease_key

        if type(step_index) is not int or step_index < 0:
            raise ValueError("workflow step index must be a non-negative integer")
        self.leases.assert_valid(session, lease)
        if parent_ref is None or lease.lease_key != task_dispatch_lease_key(parent_ref.issuer_domain, parent_ref.id):
            raise ValueError("dispatch lease is not bound to the workflow parent task")
        row = session.execute(
            select(TaskStateRow)
            .where(
                TaskStateRow.task_issuer == parent_ref.issuer_domain,
                TaskStateRow.task_id == parent_ref.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None or TaskLifecycleState(row.lifecycle_state) is not TaskLifecycleState.RUNNING:
            raise ValueError("the workflow parent is not running; this step cannot start")
        closed = (row.extra or {}).get("workflow_step_states") or {}
        if str(step_index) in closed:
            raise ValueError(f"workflow step {step_index} is closed; late dispatch is refused")

    def assert_workflow_step_dispatchable(self, parent_ref: BackendRef, step_index: int, lease: LeaseHandle) -> None:
        """Read the creation fence before reserving quota.

        The quota caller holds its own scope lock. Use the read engine here
        so SQLite does not try to acquire a second writer lock. Child
        creation rechecks the fence in its own write transaction.
        """
        with self._read_session() as session:
            self._assert_step_dispatchable(session, parent_ref, step_index, lease)

    def update_task_extra(
        self,
        task_ref: BackendRef,
        extra_update: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> TaskStateRecord:
        """Merge ``extra_update`` into a task's extra without a lifecycle move.

        The reclaim sweep's step-close markers land through this writer: the
        parent is already terminal there, where ``apply_task_state`` refuses
        the same-state write. Revision CAS + the row lock keep concurrent
        marker writers honest; the ``task_state`` event re-arms the relay's
        sweep so an interruption between close and release re-converges.
        """

        with self._session() as session, session.begin():
            row = session.execute(
                select(TaskStateRow)
                .where(
                    TaskStateRow.task_issuer == task_ref.issuer_domain,
                    TaskStateRow.task_id == task_ref.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"task {task_ref.id} has no durable state to update")
            if expected_revision is not None and expected_revision != row.revision:
                raise ValueError(f"stale revision: expected {expected_revision}, current is {row.revision}")
            if extra_update:
                row.extra = {**(row.extra or {}), **extra_update}
            row.revision += 1
            row.updated_at = self._clock()
            run_ref = self._run_linkage(session, task_ref)
            if run_ref is not None:
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="task_state",
                    payload={"state": row.lifecycle_state, "revision": row.revision},
                )
            return TaskStateRecord(
                task_ref=task_ref,
                lifecycle_state=TaskLifecycleState(row.lifecycle_state),
                revision=row.revision,
                recorded_at=row.recorded_at,
                extra=dict(row.extra or {}),
            )

    def apply_task_state(
        self,
        task_ref: BackendRef,
        target: TaskLifecycleState,
        *,
        expected_revision: int | None = None,
        reconciled: bool = False,
        recorded_at: str = "",
        extra_update: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
        lease: LeaseHandle | None = None,
        event_run_ref: BackendRef | None = None,
        terminal_payload: dict[str, Any] | None = None,
        applied_command_id: str | None = None,
        input_revision: dict[str, Any] | None = None,
        expected_wait: dict[str, Any] | None = None,
        expected_wait_state: TaskLifecycleState = TaskLifecycleState.WAITING_APPROVAL,
        wait_expired: bool | None = None,
        approval_deadline: str | None = None,
        wait_clock: Callable[[], datetime] | None = None,
    ) -> TaskStateRecord:
        with self._session() as session, session.begin():
            if lease is not None:
                self.leases.assert_valid(session, lease)
            row = session.execute(
                select(TaskStateRow)
                .where(
                    TaskStateRow.task_issuer == task_ref.issuer_domain,
                    TaskStateRow.task_id == task_ref.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            # Read the commit clock after obtaining the row lock: a wake
            # may have waited for that lock across its approval deadline.
            recorded = recorded_at or self._clock()
            if expected_wait is not None:
                # Approval and ordinary human-input waits share the same
                # commit-time re-verification: the exact wait, token, run
                # linkage and effective deadline, re-checked under the row
                # lock so exactly one of {expire, cancel, wake} can land.
                wait = (row.extra or {}).get("wait") if row is not None else None
                if (
                    row is None
                    or row.lifecycle_state != expected_wait_state.value
                    or wait != expected_wait
                    or not isinstance(wait, dict)
                    or wait.get("consumed")
                    or not wait.get("wait_token")
                ):
                    raise ValueError("durable wait changed before effect commit")
                if event_run_ref is not None and (row.run_issuer, row.run_id) != (
                    event_run_ref.issuer_domain,
                    event_run_ref.id,
                ):
                    raise ValueError("durable wait run linkage changed")
                raw_deadline = wait.get("wait_expires_at")
                if raw_deadline is not None and not isinstance(raw_deadline, str):
                    raise ValueError("approval wait deadline is malformed")
                deadline = datetime.fromisoformat(raw_deadline) if raw_deadline is not None else None
                if deadline is not None and deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=UTC)
                if approval_deadline is not None:
                    authority_end = datetime.fromisoformat(approval_deadline)
                    if authority_end.tzinfo is None:
                        authority_end = authority_end.replace(tzinfo=UTC)
                    deadline = min(deadline, authority_end) if deadline is not None else authority_end
                if wait_clock is not None:
                    now = wait_clock()
                elif expected_wait_state is TaskLifecycleState.WAITING_INPUT and deadline is not None:
                    now = self._wait_now(session, recorded)
                else:
                    now = datetime.fromisoformat(recorded)
                if now.tzinfo is None:
                    now = now.replace(tzinfo=UTC)
                if wait_expired is None or (deadline is not None and deadline <= now) != wait_expired:
                    raise ValueError("approval wait deadline changed before effect commit")
            if row is None:
                if target is not TaskLifecycleState.QUEUED:
                    raise ValueError(f"first recorded state must be queued, got {target.value}")
                if expected_revision is not None and expected_revision != 0:
                    raise ValueError(f"stale revision: expected {expected_revision}, current is 0")
                revision = 0
                session.add(
                    TaskStateRow(
                        task_issuer=task_ref.issuer_domain,
                        task_id=task_ref.id,
                        lifecycle_state=target.value,
                        revision=revision,
                        recorded_at=recorded,
                        writer_source="submit",
                        created_at=recorded,
                        updated_at=recorded,
                        extra=dict(extra_update or {}),
                        input_payload=input_payload,
                    )
                )
                record = TaskStateRecord(
                    task_ref=task_ref,
                    lifecycle_state=target,
                    revision=revision,
                    recorded_at=recorded,
                    extra=dict(extra_update or {}),
                )
            else:
                current = TaskLifecycleState(row.lifecycle_state)
                validate_task_transition(current, target, reconciled=reconciled)
                if expected_revision is not None and expected_revision != row.revision:
                    raise ValueError(f"stale revision: expected {expected_revision}, current is {row.revision}")
                revision = row.revision + 1
                row.lifecycle_state = target.value
                row.revision = revision
                row.recorded_at = recorded
                row.updated_at = recorded
                if extra_update:
                    row.extra = {**(row.extra or {}), **extra_update}
                if input_payload is not None:
                    row.input_payload = input_payload
                record = TaskStateRecord(
                    task_ref=task_ref,
                    lifecycle_state=target,
                    revision=revision,
                    recorded_at=recorded,
                    extra=dict(row.extra or {}),
                )
            run_ref = self._run_linkage(session, task_ref)
            if event_run_ref is not None:
                run_ref = event_run_ref
                row = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
                if row is not None:
                    row.run_issuer, row.run_id = run_ref.issuer_domain, run_ref.id
            if run_ref is not None:
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="task_state",
                    payload={"state": target.value, "revision": revision},
                )
                if terminal_payload is not None:
                    self.events.emit(
                        session,
                        task_ref=task_ref,
                        run_ref=run_ref,
                        event_type="run_terminal",
                        payload=terminal_payload,
                    )
            new_revision_id: str | None = None
            if input_revision is not None:
                # Same transaction as the state CAS and the command flip:
                # the revision, the consumed wait and the applied receipt
                # commit together — a crash between them is impossible, and
                # the durable worker can never miss an accepted input.
                new_revision_id = self._insert_input_revision(session, task_ref, run_ref, input_revision, recorded)
            if applied_command_id is not None:
                self._stamp_applied_command(session, task_ref, applied_command_id, recorded, run_ref, new_revision_id)
            if target in (
                TaskLifecycleState.SUCCEEDED,
                TaskLifecycleState.FAILED,
                TaskLifecycleState.CANCELLED,
            ):
                # Terminal settlement is part of the terminal fact's own
                # transaction: an unconsumed live requirement is never
                # silently lost, never applied after the fact, and never
                # keeps a task's terminal state ambiguous.
                self._settle_live_input_revisions(session, task_ref, run_ref, reason=_LIVE_SETTLEMENT_REASONS[target])
        return record

    def _stamp_applied_command(
        self,
        session: Session,
        task_ref: BackendRef,
        command_id: str,
        recorded: str,
        run_ref: BackendRef | None,
        input_revision_id: str | None,
    ) -> None:
        """Flip one command receipt to applied inside the caller's transaction.

        Shared by the wake effect commit and the live-input receive path:
        the receipt state, its effect-run binding and the input-revision
        correlation commit atomically with the effect itself.
        """

        command = session.execute(
            select(CommandRow).where(CommandRow.command_id == command_id).with_for_update()
        ).scalar_one()
        if (command.task_issuer, command.task_id) != (task_ref.issuer_domain, task_ref.id):
            raise ValueError("command is bound to another task")
        validate_command_transition(CommandState(command.state), CommandState.APPLIED)
        if command.expires_at is not None:
            deadline = datetime.fromisoformat(command.expires_at)
            now = datetime.fromisoformat(recorded)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            if deadline <= now:
                raise ValueError("command expired before effect commit")
        command.state, command.updated_at = CommandState.APPLIED.value, recorded
        if run_ref is not None:
            command.extra = {
                **(command.extra or {}),
                "effect_run_ref": run_ref.to_dict(),
            }
        if input_revision_id is not None:
            # Receipt correlation: the applied command names the input
            # revision it created (idempotent replays re-read it).
            command.extra = {
                **(command.extra or {}),
                "input_revision": input_revision_id,
            }
        if run_ref is not None:
            self.events.emit(
                session,
                task_ref=task_ref,
                run_ref=run_ref,
                event_type="command_state",
                payload={"command_id": command_id, "state": CommandState.APPLIED.value},
            )

    def record_continuation_trigger(
        self,
        task_ref: BackendRef,
        attempt: dict[str, Any],
        *,
        policy_sha256: str,
        max_continuations: int,
        expected_revision: int | None = None,
        lease: LeaseHandle | None = None,
        event_run_ref: BackendRef | None = None,
        recorded_at: str = "",
    ) -> TaskStateRecord:
        """Persist one bounded-continuation trigger fact under the row lock.

        The trigger names the exact accepted final answer a delivery gate
        rejected (candidate step id + content digest) and its failed
        standard. The append is the consume-once boundary: the same
        candidate redelivered after a crash returns the stored record
        unchanged (never a second attempt), a different candidate under a
        used index or a reached bound is corruption, and a policy digest
        that differs from the recorded block refuses to extend an old
        chain's budget. Runs in one transaction with the revision CAS and
        the governance event, like every other task-state write.
        """

        if not isinstance(attempt, dict):
            raise ValueError("continuation attempt must be an object")
        index = attempt.get("attempt")
        candidate_id = attempt.get("candidate_decision_step_id")
        candidate_digest = attempt.get("candidate_digest")
        if type(index) is not int or index < 1:
            raise ValueError("continuation attempt index must be a positive integer")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("continuation attempt requires the candidate decision step id")
        if not isinstance(candidate_digest, str) or len(candidate_digest) != 64:
            raise ValueError("continuation attempt requires the candidate content digest")
        if not isinstance(policy_sha256, str) or len(policy_sha256) != 64:
            raise ValueError("continuation policy digest must be a 64-character hex digest")
        if type(max_continuations) is not int or max_continuations < 1:
            raise ValueError("max_continuations must be a positive integer")
        with self._session() as session, session.begin():
            if lease is not None:
                self.leases.assert_valid(session, lease)
            row = session.execute(
                select(TaskStateRow)
                .where(
                    TaskStateRow.task_issuer == task_ref.issuer_domain,
                    TaskStateRow.task_id == task_ref.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("continuation trigger requires a durable task")
            if expected_revision is not None and expected_revision != row.revision:
                raise ValueError(f"stale revision: expected {expected_revision}, current is {row.revision}")
            current = TaskLifecycleState(row.lifecycle_state)
            if current is not TaskLifecycleState.RUNNING:
                raise ValueError(f"continuation trigger requires a running task, got {current.value}")
            block = (row.extra or {}).get("continuation")
            if block is None:
                block = {"policy_sha256": policy_sha256, "max_continuations": max_continuations, "attempts": []}
            if not isinstance(block, dict) or not isinstance(block.get("attempts"), list):
                raise ValueError("stored continuation block is corrupt")
            if block.get("policy_sha256") != policy_sha256:
                raise ValueError("continuation policy changed after its first recorded trigger")
            attempts = block["attempts"]
            if block.get("max_continuations") != max_continuations:
                raise ValueError("continuation bound changed after its first recorded trigger")
            # Detach every nested object BEFORE writing: mutating the loaded
            # JSON structures in place would make the "old" and "new" column
            # values compare equal, so the UPDATE would silently drop the
            # extra change while still bumping the revision.
            detached: list[dict[str, Any]] = []
            for existing in attempts:
                if not isinstance(existing, dict):
                    raise ValueError("stored continuation attempt is corrupt")
                detached.append(dict(existing))
                if existing.get("candidate_decision_step_id") == candidate_id:
                    if existing == attempt:
                        # Already consumed by a dispatch that died before its
                        # next model call: replay re-reads the stored facts.
                        return TaskStateRecord(
                            task_ref=task_ref,
                            lifecycle_state=current,
                            revision=row.revision,
                            recorded_at=row.recorded_at,
                            writer_source=row.writer_source,
                            extra=dict(row.extra or {}),
                        )
                    raise ValueError("continuation candidate is already recorded with different facts")
            if len(attempts) >= max_continuations:
                raise ValueError("continuation bound is already reached")
            if index != len(detached) + 1:
                raise ValueError("continuation attempt index must be exactly the next recorded position")
            detached.append(dict(attempt))
            recorded = recorded_at or self._clock()
            row.extra = {**(row.extra or {}), "continuation": {**block, "attempts": detached}}
            row.revision = row.revision + 1
            row.recorded_at = recorded
            row.updated_at = recorded
            run_ref = self._run_linkage(session, task_ref)
            if event_run_ref is not None:
                run_ref = event_run_ref
            if run_ref is not None:
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="task_continuation",
                    payload={
                        "attempt": index,
                        "candidate_decision_step_id": candidate_id,
                        "candidate_digest": candidate_digest,
                        "status": attempt.get("status"),
                    },
                )
            return TaskStateRecord(
                task_ref=task_ref,
                lifecycle_state=current,
                revision=row.revision,
                recorded_at=recorded,
                writer_source=row.writer_source,
                extra=dict(row.extra or {}),
            )

    def _run_linkage(self, session: Session, task_ref: BackendRef) -> BackendRef | None:
        # A select (not session.get) so autoflush surfaces a task row added
        # earlier in this same transaction.
        row = session.execute(
            select(TaskStateRow.run_issuer, TaskStateRow.run_id).where(
                TaskStateRow.task_issuer == task_ref.issuer_domain,
                TaskStateRow.task_id == task_ref.id,
            )
        ).first()
        if row is None or row[0] is None or row[1] is None:
            return None
        return _run_ref(row[0], row[1])

    def _insert_input_revision(
        self,
        session: Session,
        task_ref: BackendRef,
        run_ref: BackendRef | None,
        revision: dict[str, Any],
        recorded: str,
    ) -> str:
        """Idempotently insert one input revision inside the caller's transaction.

        The unique (task, source_ref) key arbitrates concurrent or replayed
        acceptance of the same command: the first insert wins and replays
        return the original revision identity. A digest mismatch on the same
        source_ref is a conflicting command, not a second revision. A live
        supplement (explicit ``effective_turn=None``) persists unbound; the
        lease holder binds it at the next not-yet-started decision boundary
        in its own transaction.
        """

        import json as _json
        import uuid as _uuid
        from hashlib import sha256

        from .models import AcceptedDecisionRow, InputRevisionRow

        source = revision.get("source")
        source_ref = revision.get("source_ref")
        content = revision.get("content")
        effective_turn: Any = revision.get("effective_turn")
        if effective_turn is None and source != LIVE_INPUT_REVISION_SOURCE:
            effective_turn = _COMPUTE_INPUT_BOUNDARY
        if not isinstance(source, str) or not source.strip() or len(source) > 32:
            raise ValueError("input revision source must be a nonblank string")
        if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 256:
            raise ValueError("input revision source_ref must be a nonblank string of at most 256 characters")
        if not isinstance(content, dict):
            raise ValueError("input revision content must be a JSON object")
        if source == LIVE_INPUT_REVISION_SOURCE and (
            "approval_ref" in content or "wait_token" in content or "agent_child_result" in content
        ):
            # Free supplement text must never carry approval, wait or
            # child-result control facts — those move only through their
            # own verified paths.
            raise ValueError("live input content must not carry approval or wait control keys")
        if (
            effective_turn is not _COMPUTE_INPUT_BOUNDARY
            and effective_turn is not None
            and (type(effective_turn) is not int or not 1 <= effective_turn <= 10**6)
        ):
            raise ValueError("input revision effective_turn must be a positive integer")
        if run_ref is None:
            raise ValueError("input revision requires a linked Run")
        digest = sha256(
            _json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()
        existing = session.execute(
            select(InputRevisionRow).where(
                InputRevisionRow.task_issuer == task_ref.issuer_domain,
                InputRevisionRow.task_id == task_ref.id,
                InputRevisionRow.source_ref == source_ref,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.content_sha256 != digest or (
                existing.source,
                existing.effective_turn,
                existing.wait_token,
                existing.run_issuer,
                existing.run_id,
            ) != (
                source,
                existing.effective_turn if not isinstance(effective_turn, int) else effective_turn,
                revision.get("wait_token"),
                run_ref.issuer_domain,
                run_ref.id,
            ):
                raise ValueError("input revision command is bound to different content")
            return existing.revision_id
        if effective_turn is _COMPUTE_INPUT_BOUNDARY:
            # The task row is locked by apply_task_state: boundary selection
            # and accepted input/receipt publication use this transaction.
            effective_turn = (
                int(
                    session.execute(
                        select(func.count()).where(
                            AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                            AcceptedDecisionRow.run_id == run_ref.id,
                        )
                    ).scalar()
                    or 0
                )
                + 1
            )
        seq = (
            session.execute(
                select(func.max(InputRevisionRow.seq)).where(
                    InputRevisionRow.task_issuer == task_ref.issuer_domain,
                    InputRevisionRow.task_id == task_ref.id,
                )
            ).scalar()
            or 0
        ) + 1
        revision_id = revision.get("revision_id")
        if not isinstance(revision_id, str) or not revision_id.strip():
            revision_id = f"rev-{_uuid.uuid4().hex}"
        if len(revision_id) > 64 or revision_id.strip() != revision_id:
            raise ValueError("input revision identity must be trimmed and at most 64 characters")
        wait_token = revision.get("wait_token")
        if wait_token is not None and (not isinstance(wait_token, str) or len(wait_token) > 256):
            raise ValueError("input revision wait_token must be a string of at most 256 characters")
        session.add(
            InputRevisionRow(
                revision_id=revision_id,
                task_issuer=task_ref.issuer_domain,
                task_id=task_ref.id,
                run_issuer=run_ref.issuer_domain if run_ref is not None else "",
                run_id=run_ref.id if run_ref is not None else "",
                seq=seq,
                source=source[:32],
                source_ref=source_ref[:256],
                content=dict(content),
                content_sha256=digest,
                wait_token=wait_token,
                effective_turn=effective_turn,
                created_at=recorded,
            )
        )
        if run_ref is not None:
            self.events.emit(
                session,
                task_ref=task_ref,
                run_ref=run_ref,
                event_type="input_revision",
                payload={
                    "revision_id": revision_id,
                    "source": source[:32],
                    "effective_turn": effective_turn,
                    "seq": seq,
                },
            )
        return revision_id

    def list_input_revisions(self, task_ref: BackendRef, run_ref: BackendRef | None = None) -> list[dict[str, Any]]:
        """The task's replayable input history in causal order (oldest first).

        Order is (effective_turn, seq) — the same order replay folds into
        the rebuilt conversation. Pending live supplements (unbound) and
        settled (not-applied) rows are real durable facts but not replay
        input, so they are excluded here; :meth:`list_input_revisions_with_delivery`
        is their public read. A stored digest mismatch is corruption:
        surfaced as an explicit error, never silently reserialized.
        """

        import json as _json
        from hashlib import sha256

        from .models import AcceptedDecisionRow, InputRevisionRow

        with self._read_session() as session:
            rows = (
                session.execute(
                    select(InputRevisionRow)
                    .where(
                        InputRevisionRow.task_issuer == task_ref.issuer_domain,
                        InputRevisionRow.task_id == task_ref.id,
                    )
                    .order_by(InputRevisionRow.effective_turn, InputRevisionRow.seq)
                )
                .scalars()
                .all()
            )
            # Applied receipts and accepted decisions are independent durable
            # references to the history. An empty result after row loss must
            # not look like a run that never received human input.
            required_ids = {
                extra["input_revision"]
                for extra in session.execute(
                    select(CommandRow.extra).where(
                        CommandRow.task_issuer == task_ref.issuer_domain,
                        CommandRow.task_id == task_ref.id,
                        CommandRow.state == CommandState.APPLIED.value,
                    )
                ).scalars()
                if isinstance(extra, dict) and extra.get("input_revision")
            }
            current_run = run_ref or self._run_linkage(session, task_ref)
            if current_run is not None:
                for refs in session.execute(
                    select(AcceptedDecisionRow.input_refs).where(
                        AcceptedDecisionRow.issuer_domain == current_run.issuer_domain,
                        AcceptedDecisionRow.run_id == current_run.id,
                    )
                ).scalars():
                    required_ids.update(refs.get("inputs") or [])
            if not required_ids.issubset({row.revision_id for row in rows}):
                raise ValueError("input revision history is incomplete")
            if sorted(row.seq for row in rows) != list(range(1, len(rows) + 1)):
                raise ValueError("input revision history sequence is incomplete or duplicated")
        revisions: list[dict[str, Any]] = []
        for row in rows:
            if run_ref is not None and (row.run_issuer, row.run_id) != (run_ref.issuer_domain, run_ref.id):
                raise ValueError("input revision history is bound to another Run")
            if row.source == LIVE_INPUT_REVISION_SOURCE and (
                row.effective_turn is None or row.settled_reason is not None
            ):
                continue
            if (
                not isinstance(row.revision_id, str)
                or not row.revision_id.strip()
                or row.revision_id.strip() != row.revision_id
                or len(row.revision_id) > 64
                or not isinstance(row.content, dict)
                or type(row.effective_turn) is not int
                or not 1 <= row.effective_turn <= 10**6
                or type(row.seq) is not int
                or row.seq < 1
            ):
                raise ValueError("input revision history has invalid content or causal metadata")
            digest = sha256(
                _json.dumps(
                    row.content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            ).hexdigest()
            if digest != row.content_sha256:
                raise ValueError(f"input revision {row.revision_id} content digest mismatch; refusing to replay")
            revisions.append(
                {
                    "revision_id": row.revision_id,
                    "seq": row.seq,
                    "source": row.source,
                    "source_ref": row.source_ref,
                    "content": dict(row.content),
                    "wait_token": row.wait_token,
                    "effective_turn": row.effective_turn,
                    "created_at": row.created_at,
                    "run_ref": {"issuer_domain": row.run_issuer, "run_id": row.run_id},
                }
            )
        return revisions

    def record_live_input_revision(
        self,
        task_ref: BackendRef,
        *,
        command_id: str,
        content: dict[str, Any],
        expected_revision: int | None = None,
        recorded_at: str = "",
    ) -> str:
        """Persist one running-task live supplement as an unbound revision.

        One transaction under the task row lock: the authoritative RUNNING
        check, the caller's revision CAS, the pending revision insert and
        the applied receipt. The task's lifecycle never changes — a live
        supplement must not requeue or park a running controller — and the
        returned revision binds only in :meth:`bind_pending_input_revisions`.
        """

        with self._session() as session, session.begin():
            row = session.execute(
                select(TaskStateRow)
                .where(
                    TaskStateRow.task_issuer == task_ref.issuer_domain,
                    TaskStateRow.task_id == task_ref.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("live input requires a recorded task")
            if TaskLifecycleState(row.lifecycle_state) is not TaskLifecycleState.RUNNING:
                raise ValueError(f"task is {row.lifecycle_state}; live input requires a running task")
            if expected_revision is not None and expected_revision != row.revision:
                raise ValueError(f"stale revision: expected {expected_revision}, current is {row.revision}")
            recorded = recorded_at or self._clock()
            run_ref = self._run_linkage(session, task_ref)
            if run_ref is None:
                raise ValueError("live input requires a linked Run")
            revision_id = self._insert_input_revision(
                session,
                task_ref,
                run_ref,
                {
                    "source": LIVE_INPUT_REVISION_SOURCE,
                    "source_ref": command_id,
                    "content": content,
                    "effective_turn": None,
                },
                recorded,
            )
            command = session.execute(
                select(CommandRow).where(CommandRow.command_id == command_id).with_for_update()
            ).scalar_one_or_none()
            if command is not None and command.state == CommandState.APPLIED.value:
                # Idempotent replay of an already-received command: the
                # original receipt and its revision correlation stand; a
                # mismatch here is corruption, not a second receipt.
                if command.extra.get("input_revision") != revision_id:
                    raise ValueError("live input command is bound to a different revision")
            else:
                self._stamp_applied_command(session, task_ref, command_id, recorded, run_ref, revision_id)
        return revision_id

    def count_pending_live_input_revisions(self, task_ref: BackendRef, run_ref: BackendRef) -> int:
        """Received-but-unbound live supplement count for the run's boundary gate."""

        from .models import InputRevisionRow

        with self._read_session() as session:
            return int(
                session.execute(
                    select(func.count()).where(
                        InputRevisionRow.task_issuer == task_ref.issuer_domain,
                        InputRevisionRow.task_id == task_ref.id,
                        InputRevisionRow.run_issuer == run_ref.issuer_domain,
                        InputRevisionRow.run_id == run_ref.id,
                        InputRevisionRow.source == LIVE_INPUT_REVISION_SOURCE,
                        InputRevisionRow.effective_turn.is_(None),
                        InputRevisionRow.settled_reason.is_(None),
                    )
                ).scalar()
                or 0
            )

    def bind_pending_input_revisions(
        self,
        task_ref: BackendRef,
        run_ref: BackendRef,
        *,
        lease: LeaseHandle,
    ) -> list[dict[str, Any]]:
        """Bind received live supplements at the next not-yet-started boundary.

        Called by the lease-holding host only after the loop parked between
        turns, so no model call is in flight: the boundary is the committed
        decision count plus one, computed under the task row lock, and the
        decision fence makes concurrent commits impossible. Returns the
        bound revisions in durable order; ``[]`` when the task is no longer
        RUNNING (the caller's cancel path wins) or nothing is pending. A
        crash after binding replays deterministically: recovery folds the
        revision at its recorded boundary exactly once.
        """

        from .models import AcceptedDecisionRow, InputRevisionRow

        if lease.lease_key != task_dispatch_lease_key(task_ref.issuer_domain, task_ref.id):
            raise ValueError("live input binding requires the task's dispatch lease")
        with self._session() as session, session.begin():
            self.leases.assert_valid(session, lease)
            row = session.execute(
                select(TaskStateRow)
                .where(
                    TaskStateRow.task_issuer == task_ref.issuer_domain,
                    TaskStateRow.task_id == task_ref.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None or TaskLifecycleState(row.lifecycle_state) is not TaskLifecycleState.RUNNING:
                return []
            if (row.run_issuer, row.run_id) != (run_ref.issuer_domain, run_ref.id):
                raise ValueError("live input binding requires the task's current Run")
            pending = (
                session.execute(
                    select(InputRevisionRow)
                    .where(
                        InputRevisionRow.task_issuer == task_ref.issuer_domain,
                        InputRevisionRow.task_id == task_ref.id,
                        InputRevisionRow.run_issuer == run_ref.issuer_domain,
                        InputRevisionRow.run_id == run_ref.id,
                        InputRevisionRow.source == LIVE_INPUT_REVISION_SOURCE,
                        InputRevisionRow.effective_turn.is_(None),
                        InputRevisionRow.settled_reason.is_(None),
                    )
                    .order_by(InputRevisionRow.seq)
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            if not pending:
                return []
            boundary = (
                int(
                    session.execute(
                        select(func.count()).where(
                            AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                            AcceptedDecisionRow.run_id == run_ref.id,
                        )
                    ).scalar()
                    or 0
                )
                + 1
            )
            bound: list[dict[str, Any]] = []
            for item in pending:
                item.effective_turn = boundary
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="input_revision_bound",
                    payload={"revision_id": item.revision_id, "effective_turn": boundary, "seq": item.seq},
                )
                bound.append(
                    {
                        "revision_id": item.revision_id,
                        "seq": item.seq,
                        "effective_turn": boundary,
                        "content": dict(item.content),
                    }
                )
        return bound

    def list_input_revisions_with_delivery(self, task_ref: BackendRef) -> list[dict[str, Any]]:
        """Whitelisted delivery view of one task's live supplements.

        Derives each revision's lifecycle from the authoritative facts —
        the persisted row (receipt, binding boundary, settlement reason)
        and the accepted decision that pinned it (consumption, with the
        consuming decision's identity and turn). Content, tokens and
        storage internals are never part of this view.
        """

        from .models import AcceptedDecisionRow, InputRevisionRow

        with self._read_session() as session:
            rows = (
                session.execute(
                    select(InputRevisionRow)
                    .where(
                        InputRevisionRow.task_issuer == task_ref.issuer_domain,
                        InputRevisionRow.task_id == task_ref.id,
                        InputRevisionRow.source == LIVE_INPUT_REVISION_SOURCE,
                    )
                    .order_by(InputRevisionRow.seq)
                )
                .scalars()
                .all()
            )
            consumed_by: dict[tuple[str, str, str], tuple[str, int]] = {}
            run_keys = sorted({(row.run_issuer, row.run_id) for row in rows})
            if run_keys:
                decision_rows = session.execute(
                    select(
                        AcceptedDecisionRow.issuer_domain,
                        AcceptedDecisionRow.run_id,
                        AcceptedDecisionRow.decision_step_id,
                        AcceptedDecisionRow.input_refs,
                    ).where(tuple_(AcceptedDecisionRow.issuer_domain, AcceptedDecisionRow.run_id).in_(run_keys))
                ).all()
                for issuer, run_id, step_id, refs in decision_rows:
                    if not isinstance(refs, dict):
                        continue
                    turn = refs.get("turn")
                    if type(turn) is not int:
                        continue
                    for revision_id in refs.get("inputs") or []:
                        # Later decisions re-pin prior inputs. The database
                        # need not return the first consuming turn first.
                        key = (issuer, run_id, str(revision_id))
                        previous = consumed_by.get(key)
                        if previous is None or turn < previous[1]:
                            consumed_by[key] = (step_id, turn)
            items: list[dict[str, Any]] = []
            for row in rows:
                consumer = consumed_by.get((row.run_issuer, row.run_id, row.revision_id))
                if row.settled_reason is not None:
                    status = "not_applied"
                elif row.effective_turn is None:
                    status = "received"
                elif consumer is not None:
                    status = "consumed"
                else:
                    status = "bound"
                item: dict[str, Any] = {
                    "source_ref": row.source_ref,
                    "status": status,
                    "revision_id": row.revision_id,
                    "run_ref": _run_ref(row.run_issuer, row.run_id).to_dict(),
                }
                if status in ("bound", "consumed") and row.effective_turn is not None:
                    item["effective_turn"] = row.effective_turn
                if status == "consumed" and consumer is not None:
                    step_id, turn = consumer
                    item["consumed_by"] = {"decision_step_id": step_id, "turn": turn}
                if status == "not_applied":
                    item["settled_reason"] = row.settled_reason
                items.append(item)
            return items

    def _settle_live_input_revisions(
        self, session: Session, task_ref: BackendRef, run_ref: BackendRef | None, *, reason: str
    ) -> None:
        """Settle unconsumed live supplements inside the caller's transaction.

        Rows an accepted decision already pinned are consumed facts and
        stay untouched; received (unbound) and bound-but-unconsumed rows
        settle to ``not_applied`` with the terminal reason. Scoped to the
        live source and the revision's own run: waiting_input wake
        revisions keep their vocabulary, and settlement never revives or
        rewrites a consumed requirement.
        """

        from .models import AcceptedDecisionRow, InputRevisionRow

        query = select(InputRevisionRow).where(
            InputRevisionRow.task_issuer == task_ref.issuer_domain,
            InputRevisionRow.task_id == task_ref.id,
            InputRevisionRow.source == LIVE_INPUT_REVISION_SOURCE,
            InputRevisionRow.settled_reason.is_(None),
        )
        if run_ref is not None:
            query = query.where(
                InputRevisionRow.run_issuer == run_ref.issuer_domain,
                InputRevisionRow.run_id == run_ref.id,
            )
        rows = session.execute(query.order_by(InputRevisionRow.seq)).scalars().all()
        if not rows:
            return
        pinned: set[str] = set()
        if run_ref is not None:
            for refs in session.execute(
                select(AcceptedDecisionRow.input_refs).where(
                    AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                    AcceptedDecisionRow.run_id == run_ref.id,
                )
            ).scalars():
                if isinstance(refs, dict):
                    pinned.update(str(item) for item in refs.get("inputs") or [])
        for row in rows:
            if row.revision_id in pinned:
                continue
            row.settled_reason = reason

    def count_accepted_decisions(self, run_ref: BackendRef) -> int:
        """Committed decision count of one run — the input-revision boundary.

        Both issuer and run id define the decision namespace. Another
        issuer's same-named run must never move this run's input boundary.
        """

        from .models import AcceptedDecisionRow

        require_kind(run_ref, RefKind.RUN)
        with self._read_session() as session:
            return int(
                session.execute(
                    select(func.count()).where(
                        AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                        AcceptedDecisionRow.run_id == run_ref.id,
                    )
                ).scalar()
                or 0
            )

    def get_task_state(self, task_ref: BackendRef) -> TaskStateRecord | None:
        with self._read_session() as session:
            row = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
            return None if row is None else _task_record_of(row)

    def attach_workspace(self, task_ref: BackendRef, workspace_id: Any) -> None:
        """Platform attribution for scoped queries; the standalone host ignores it."""

        with self._session() as session, session.begin():
            session.execute(
                update(TaskStateRow)
                .where(
                    TaskStateRow.task_issuer == task_ref.issuer_domain,
                    TaskStateRow.task_id == task_ref.id,
                )
                .values(workspace_id=str(workspace_id))
            )

    def get_task_input(self, task_ref: BackendRef) -> dict[str, Any] | None:
        """Host-written replay input for one task (or ``None``)."""

        with self._read_session() as session:
            row = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
            return None if row is None else row.input_payload

    def list_tasks(self, states: set[TaskLifecycleState] | None = None) -> list[TaskStateRecord]:
        """All task records, optionally filtered by lifecycle state."""

        with self._read_session() as session:
            rows = session.execute(select(TaskStateRow).order_by(TaskStateRow.created_at)).scalars().all()
        records = [_task_record_of(row) for row in rows]
        if states is None:
            return records
        return [record for record in records if record.lifecycle_state in states]

    def list_task_page(
        self,
        states: set[TaskLifecycleState],
        *,
        after: tuple[str, str, str] | None = None,
        limit: int = 32,
    ) -> tuple[list[TaskStateRecord], tuple[str, str, str] | None]:
        """Read a bounded keyset page for the platform wait reconciler."""
        if limit < 1:
            raise ValueError("task page limit must be positive")
        position = tuple_(TaskStateRow.created_at, TaskStateRow.task_issuer, TaskStateRow.task_id)
        query = select(TaskStateRow).where(TaskStateRow.lifecycle_state.in_([state.value for state in states]))
        if after is not None:
            query = query.where(position > tuple_(*after))
        query = query.order_by(TaskStateRow.created_at, TaskStateRow.task_issuer, TaskStateRow.task_id).limit(limit)
        with self._read_session() as session:
            rows = session.execute(query).scalars().all()
        cursor = (rows[-1].created_at, rows[-1].task_issuer, rows[-1].task_id) if len(rows) == limit else None
        return [_task_record_of(row) for row in rows], cursor

    # -- ControlCommandRecorder ------------------------------------------------

    def record(self, command: ControlCommandRecord) -> ControlCommandRecord:
        for attempt in range(3):
            try:
                with self._session() as session, session.begin():
                    existing = session.get(CommandRow, command.command_id)
                    if existing is not None:
                        saved = _command_of(existing).to_dict()
                        incoming = command.to_dict()
                        if {k: v for k, v in saved.items() if k != "state"} != {
                            k: v for k, v in incoming.items() if k != "state"
                        }:
                            raise ValueError(f"command {command.command_id!r} already recorded with different content")
                        return _command_of(existing)
                    session.add(
                        CommandRow(
                            command_id=command.command_id,
                            kind=command.kind.value,
                            issuer=command.issuer,
                            task_issuer=command.task_ref.issuer_domain,
                            task_id=command.task_ref.id,
                            issued_at=command.issued_at,
                            state=command.state.value,
                            workspace_id=command.extra.get("workspace_id"),
                            run_issuer=command.run_ref.issuer_domain if command.run_ref else None,
                            run_id=command.run_ref.id if command.run_ref else None,
                            expires_at=command.expires_at,
                            expected_revision=command.expected_revision,
                            payload=command.payload,
                            payload_schema_ref=command.payload_schema_ref,
                            detail_ns=command.detail_ns,
                            extra=command.extra,
                            updated_at=self._clock(),
                        )
                    )
                    session.flush()
                    run_ref = command.run_ref or self._run_linkage(session, command.task_ref)
                    if run_ref is not None:
                        self.events.emit(
                            session,
                            task_ref=command.task_ref,
                            run_ref=run_ref,
                            event_type="command_recorded",
                            payload={"command_id": command.command_id, "kind": command.kind.value},
                            correlation_id=command.command_id,
                        )
                return command
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def transition(self, command_id: str, target: CommandState) -> ControlCommandRecord:
        with self._session() as session, session.begin():
            row = session.execute(
                select(CommandRow).where(CommandRow.command_id == command_id).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"unknown command {command_id!r}")
            validate_command_transition(CommandState(row.state), target)
            changed = session.execute(
                update(CommandRow)
                .where(CommandRow.command_id == command_id, CommandRow.state == row.state)
                .values(state=target.value, updated_at=self._clock())
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                raise ValueError("command state changed concurrently")
            session.refresh(row)
            record = _command_of(row)
            task_ref = _task_ref(row.task_issuer, row.task_id)
            run_ref = (
                _run_ref(row.run_issuer, row.run_id)
                if row.run_issuer and row.run_id
                else self._run_linkage(session, task_ref)
            )
            if run_ref is not None:
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="command_state",
                    payload={"command_id": command_id, "state": target.value},
                    correlation_id=command_id,
                )
        return record

    def get(self, command_id: str) -> ControlCommandRecord | None:
        with self._read_session() as session:
            row = session.get(CommandRow, command_id)
            return None if row is None else _command_of(row)

    def list_pending_commands(self, workspace_id: str) -> list[ControlCommandRecord]:
        """Workspace-scoped ``requested`` receipts (lazy-expiry sweep input)."""

        with self._read_session() as session:
            rows = (
                session.execute(
                    select(CommandRow).where(
                        CommandRow.workspace_id == str(workspace_id),
                        CommandRow.state == CommandState.REQUESTED.value,
                    )
                )
                .scalars()
                .all()
            )
            return [_command_of(row) for row in rows]

    def list_task_commands(
        self,
        workspace_id: str,
        task_ref: BackendRef,
        limit: int,
        offset: int,
    ) -> tuple[list[ControlCommandRecord], int]:
        """Read-only page of one task's receipts; filter/sort/count in SQL."""

        require_kind(task_ref, RefKind.TASK)
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid command pagination")

        scope = (
            CommandRow.workspace_id == str(workspace_id),
            CommandRow.task_issuer == task_ref.issuer_domain,
            CommandRow.task_id == task_ref.id,
        )
        # Preserve the contract's ordering over the stored ISO string.
        order = (CommandRow.issued_at.desc(), CommandRow.command_id.desc())
        with self._read_session() as session:
            total = int(session.execute(select(func.count()).select_from(CommandRow).where(*scope)).scalar_one())
            rows = (
                session.execute(select(CommandRow).where(*scope).order_by(*order).limit(limit).offset(offset))
                .scalars()
                .all()
            )
            return [_command_of(row) for row in rows], total

    # -- ActionLedger ----------------------------------------------------------

    def record_intent(self, intent: ActionIntent) -> None:
        self.record_intent_ex(intent)

    def record_intent_ex(
        self,
        intent: ActionIntent,
        *,
        task_ref: BackendRef | None = None,
        run_ref: BackendRef | None = None,
        session_id: str | None = None,
        execution_id: str | None = None,
        tool_call_id: str | None = None,
        lease: LeaseHandle | None = None,
    ) -> None:
        for attempt in range(3):
            try:
                with self._session() as session, session.begin():
                    if lease is not None:
                        self.leases.assert_valid(session, lease)
                    existing = session.get(ActionIntentRow, intent.action_key)
                    if existing is not None:
                        if (
                            existing.arguments_digest != intent.arguments_digest
                            or existing.action_name != intent.action_name
                            or existing.side_effect_class != intent.side_effect_class.value
                        ):
                            raise IdempotencyConflictError(intent.action_key, existing.arguments_digest)
                        return
                    now = self._clock()
                    session.add(
                        ActionIntentRow(
                            action_key=intent.action_key,
                            action_name=intent.action_name,
                            arguments_digest=intent.arguments_digest,
                            side_effect_class=intent.side_effect_class.value,
                            task_issuer=task_ref.issuer_domain if task_ref else None,
                            task_id=task_ref.id if task_ref else None,
                            run_issuer=run_ref.issuer_domain if run_ref else None,
                            run_id=run_ref.id if run_ref else None,
                            session_id=session_id,
                            execution_id=execution_id,
                            tool_call_id=tool_call_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    session.flush()
                    if task_ref is not None and run_ref is not None:
                        self.events.emit(
                            session,
                            task_ref=task_ref,
                            run_ref=run_ref,
                            event_type="action_intent",
                            payload={
                                "action_key": intent.action_key,
                                "action_name": intent.action_name,
                                "arguments_digest": intent.arguments_digest,
                                "side_effect_class": intent.side_effect_class.value,
                                "execution_id": execution_id,
                                "tool_call_id": tool_call_id,
                            },
                        )
                return
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def claim(self, action_key: str) -> ClaimReceipt:
        receipt, _token = self.claim_ex(action_key, holder=None)
        return receipt

    def claim_ex(
        self, action_key: str, *, holder: str | None = None, lease: LeaseHandle | None = None
    ) -> tuple[ClaimReceipt, int | None]:
        """Atomic claim: at most one concurrent claimer flips the slot.

        The winner receives the incremented claim token (the per-action
        fencing token for fenced outcome writes); losers get the pre-claim
        recovery verdict without execution rights.
        """

        with self._session() as session, session.begin():
            if lease is not None:
                self.leases.assert_valid(session, lease)
            row = session.execute(
                select(ActionIntentRow).where(ActionIntentRow.action_key == action_key).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"action {action_key!r} has no recorded intent; record intent before claiming")
            outcome = session.get(ActionOutcomeRow, action_key)
            recovery = self._recovery_of_rows(action_key, row, outcome)
            if not self._claimable(row, outcome) or row.active:
                return ClaimReceipt(action_key=action_key, claimed=False, recovery=recovery), None
            claimed_at = self._clock()
            # Capture the token before the CAS: synchronize_session would
            # otherwise apply the increment to the in-memory row and skew the
            # token handed to the winner.
            base_token = row.claim_token
            result = session.execute(
                update(ActionIntentRow)
                .where(ActionIntentRow.action_key == action_key, ActionIntentRow.active.is_(False))
                .values(
                    active=True,
                    claim_token=ActionIntentRow.claim_token + 1,
                    claim_holder=holder,
                    claimed_at=claimed_at,
                    updated_at=claimed_at,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                # The FOR UPDATE row lock serializes concurrent claimants on
                # PostgreSQL; BEGIN IMMEDIATE serializes writers on SQLite.
                # Reaching here means the slot was taken between the read and
                # the CAS - report the loser verdict from a fresh read.
                fresh = session.execute(
                    select(ActionIntentRow).where(ActionIntentRow.action_key == action_key)
                ).scalar_one_or_none()
                if fresh is None:  # pragma: no cover - intent cannot vanish
                    raise KeyError(action_key)
                recovery = self._recovery_of_rows(action_key, fresh, session.get(ActionOutcomeRow, action_key))
                return ClaimReceipt(action_key=action_key, claimed=False, recovery=recovery), None
            token = base_token + 1
            task_ref, run_ref = _intent_linkage(row)
            if task_ref is not None and run_ref is not None:
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="action_claimed",
                    payload={"action_key": action_key, "claim_token": token, "holder": holder},
                )
            return ClaimReceipt(action_key=action_key, claimed=True, recovery=recovery), token

    def record_outcome(self, outcome: ActionOutcomeRecord) -> None:
        self.record_outcome_ex(outcome)

    def record_outcome_ex(
        self,
        outcome: ActionOutcomeRecord,
        *,
        claim_token: int | None = None,
        result_payload: Any = None,
        lease: LeaseHandle | None = None,
    ) -> None:
        """Record the real outcome; ``claim_token`` enables late-receipt fencing.

        With a token the write is a conditional UPDATE on the claim slot: a
        stale token (another executor re-claimed after a lease expiry) is
        rejected — the authoritative outcome stays untouched and the late
        receipt is journaled as an event for reconciliation. Without a token
        this is the seam-level single-writer write.

        A dispatch ``lease`` instead confirms an unfinished idempotent action
        from host-owned facts. Platform Agent child recovery consumes this
        path; it never grants a retry of an unknown external effect.
        """

        if lease is not None and claim_token is not None:
            raise ValueError("choose a claim token or a host confirmation lease")
        if claim_token is not None:
            self._record_outcome_fenced(outcome, claim_token=claim_token, result_payload=result_payload)
            return
        with self._session() as session, session.begin():
            if lease is not None:
                self.leases.assert_valid(session, lease)
            intent = session.execute(
                select(ActionIntentRow).where(ActionIntentRow.action_key == outcome.action_key).with_for_update()
            ).scalar_one_or_none()
            if intent is None:
                raise KeyError(f"action {outcome.action_key!r} has no recorded intent")
            if lease is not None:
                from hecate_durable.storage.lease import task_dispatch_lease_key

                task_ref, run_ref = _intent_linkage(intent)
                task = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id)) if task_ref else None
                if (
                    task_ref is None
                    or run_ref is None
                    or lease.lease_key != task_dispatch_lease_key(task_ref.issuer_domain, task_ref.id)
                    or intent.side_effect_class != ToolSideEffectClass.IDEMPOTENT_WRITE.value
                    or outcome.outcome is not ActionOutcome.SUCCEEDED
                    or task is None
                    or task.lifecycle_state != TaskLifecycleState.RUNNING.value
                    or (task.run_issuer, task.run_id) != (intent.run_issuer, intent.run_id)
                ):
                    raise ValueError("host confirmation is not bound to this idempotent action's dispatch lease")
                previous = session.get(ActionOutcomeRow, outcome.action_key)
                if previous is not None:
                    if previous.outcome == outcome.outcome.value and previous.result_digest == outcome.result_digest:
                        return
                    raise ValueError("host confirmation cannot replace an established action outcome")
            self._write_outcome(session, intent, outcome, result_payload)
            if lease is not None:
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="action_outcome_confirmed",
                    payload={
                        "action_key": outcome.action_key,
                        "holder": lease.holder,
                        "fencing_token": lease.fencing_token,
                        "result_digest": outcome.result_digest,
                    },
                )

    def _record_outcome_fenced(self, outcome: ActionOutcomeRecord, *, claim_token: int, result_payload: Any) -> None:
        fenced = False
        try:
            with self._session() as session, session.begin():
                intent = session.execute(
                    select(ActionIntentRow).where(ActionIntentRow.action_key == outcome.action_key).with_for_update()
                ).scalar_one_or_none()
                if intent is None:
                    raise KeyError(f"action {outcome.action_key!r} has no recorded intent")
                taken = session.execute(
                    update(ActionIntentRow)
                    .where(
                        ActionIntentRow.action_key == outcome.action_key,
                        ActionIntentRow.active.is_(True),
                        ActionIntentRow.claim_token == claim_token,
                    )
                    .values(active=False, updated_at=self._clock())
                    .execution_options(synchronize_session=False)
                )
                if taken.rowcount != 1:
                    fenced = True
                    raise _FencedRejectionError
                self._write_outcome(session, intent, outcome, result_payload)
        except _FencedRejectionError:
            pass
        if fenced:
            self._journal_late_outcome(outcome, claim_token=claim_token)
            raise LateOutcomeError(outcome.action_key, claim_token)

    def _journal_late_outcome(self, outcome: ActionOutcomeRecord, *, claim_token: int) -> None:
        """Journal the rejected late receipt as an event; never touch state."""

        try:
            with self._session() as session, session.begin():
                intent = session.get(ActionIntentRow, outcome.action_key)
                if intent is None:  # pragma: no cover - fenced write implies intent
                    return
                task_ref, run_ref = _intent_linkage(intent)
                if task_ref is None or run_ref is None:
                    return
                self.events.emit(
                    session,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    event_type="late_outcome_rejected",
                    payload={
                        "action_key": outcome.action_key,
                        "outcome": outcome.outcome.value,
                        "stale_claim_token": claim_token,
                        "current_claim_token": intent.claim_token,
                        "active": intent.active,
                    },
                )
        except SQLAlchemyError:
            logger.exception("Could not journal late outcome for %s", outcome.action_key)

    def _write_outcome(
        self, session: Session, intent: ActionIntentRow, outcome: ActionOutcomeRecord, result_payload: Any
    ) -> None:
        now = self._clock()
        existing = session.get(ActionOutcomeRow, outcome.action_key)
        if existing is None:
            session.add(
                ActionOutcomeRow(
                    action_key=outcome.action_key,
                    outcome=outcome.outcome.value,
                    result_digest=outcome.result_digest,
                    result_ref=outcome.result_ref.to_dict() if outcome.result_ref else None,
                    result_payload=result_payload,
                    recorded_at=now,
                )
            )
        else:
            existing.outcome = outcome.outcome.value
            existing.result_digest = outcome.result_digest
            existing.result_ref = outcome.result_ref.to_dict() if outcome.result_ref else None
            existing.result_payload = result_payload
            existing.recorded_at = now
        session.execute(
            update(ActionIntentRow)
            .where(ActionIntentRow.action_key == outcome.action_key)
            .values(active=False, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        task_ref, run_ref = _intent_linkage(intent)
        if task_ref is not None and run_ref is not None:
            self.events.emit(
                session,
                task_ref=task_ref,
                run_ref=run_ref,
                event_type="action_outcome",
                payload={
                    "action_key": outcome.action_key,
                    "outcome": outcome.outcome.value,
                    "result_digest": outcome.result_digest,
                    "has_result_ref": outcome.result_ref is not None,
                },
            )

    def recovery(self, action_key: str) -> ActionRecovery:
        try:
            return self._recovery(action_key)
        except Exception:
            # A failed lookup is store_unavailable — never a downgrade to
            # never_started. Broad on purpose: transport, dialect, and OS
            # errors all mean "the verdict could not be established".
            logger.warning("Action recovery lookup failed; state is store_unavailable", exc_info=True)
            return ActionRecovery(action_key=action_key, state=ActionLedgerState.STORE_UNAVAILABLE)

    def _recovery(self, action_key: str) -> ActionRecovery:
        with self._read_session() as session:
            intent = session.get(ActionIntentRow, action_key)
            if intent is None:
                return ActionRecovery(action_key=action_key, state=ActionLedgerState.NEVER_STARTED)
            return self._recovery_of_rows(action_key, intent, session.get(ActionOutcomeRow, action_key))

    def _recovery_of_rows(
        self, action_key: str, intent: ActionIntentRow, outcome: ActionOutcomeRow | None
    ) -> ActionRecovery:
        """Mirror ``InMemoryActionLedger._recovery_locked`` point for point."""

        intent_contract = _intent_of(intent)
        effect_class = ToolSideEffectClass(intent.side_effect_class)
        if outcome is not None and outcome.outcome == ActionOutcome.UNKNOWN.value:
            return ActionRecovery(
                action_key=action_key,
                state=ActionLedgerState.OUTCOME_UNKNOWN,
                intent=intent_contract,
                last_outcome=_outcome_of(action_key, outcome),
                pending_reconciliation=True,
            )
        if outcome is not None:
            return ActionRecovery(
                action_key=action_key,
                state=ActionLedgerState.CLAIMED,
                intent=intent_contract,
                last_outcome=_outcome_of(action_key, outcome),
                pending_reconciliation=(
                    outcome.outcome == ActionOutcome.FAILED.value
                    and not may_auto_replay(ActionLedgerState.CLAIMED, effect_class)
                ),
            )
        return ActionRecovery(
            action_key=action_key,
            state=ActionLedgerState.CLAIMED,
            intent=intent_contract,
            pending_reconciliation=not may_auto_replay(ActionLedgerState.CLAIMED, effect_class),
        )

    def _claimable(self, intent: ActionIntentRow, outcome: ActionOutcomeRow | None) -> bool:
        """Mirror ``InMemoryActionLedger._claimable_locked``."""

        if outcome is None:
            return True
        if outcome.outcome in (ActionOutcome.SUCCEEDED.value, ActionOutcome.UNKNOWN.value):
            return False
        return may_auto_replay(ActionLedgerState.CLAIMED, ToolSideEffectClass(intent.side_effect_class))

    # -- host-facing queries -----------------------------------------------------

    def wait_now(self) -> datetime:
        """Read the SQL clock, refusing an unverifiable local clock divergence."""
        with self._read_session() as session:
            return self._wait_now(session, self._clock())

    def _wait_now(self, session: Session, local_raw: str) -> datetime:
        # PostgreSQL current_timestamp is the transaction start, potentially
        # before a row-lock wait. Read actual wall time after acquiring it.
        dialect = self.engine.dialect.name
        expression = (
            func.clock_timestamp()
            if dialect == "postgresql"
            else func.strftime("%Y-%m-%dT%H:%M:%f+00:00", "now")
            if dialect == "sqlite"
            else func.current_timestamp()
        )
        raw = session.scalar(select(expression))
        now = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
        local = datetime.fromisoformat(local_raw)
        now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        local = local if local.tzinfo is not None else local.replace(tzinfo=UTC)
        if abs((local - now).total_seconds()) > 300:
            raise ValueError("input wait clock cannot be trusted")
        return now

    def action_receipt_time(
        self,
        action_key: str,
        *,
        task_ref: BackendRef | None = None,
        run_ref: BackendRef | None = None,
        action_name: str | None = None,
        arguments_digest: str | None = None,
    ) -> str | None:
        """First immutable successful receipt, optionally scoped to its caller.

        The latest outcome row can be rewritten by an idempotent receipt.
        The transactional event log preserves the original deadline anchor.
        Missing or mismatched evidence is never replaced by the current time.
        """
        from hecate_durable.storage.models import EventRow

        with self._read_session() as session:
            intent = session.get(ActionIntentRow, action_key)
            outcome = session.get(ActionOutcomeRow, action_key)
            if (
                intent is None
                or outcome is None
                or outcome.outcome != ActionOutcome.SUCCEEDED.value
                or (
                    task_ref is not None
                    and (intent.task_issuer, intent.task_id) != (task_ref.issuer_domain, task_ref.id)
                )
                or (run_ref is not None and (intent.run_issuer, intent.run_id) != (run_ref.issuer_domain, run_ref.id))
                or (action_name is not None and intent.action_name != action_name)
                or (arguments_digest is not None and intent.arguments_digest != arguments_digest)
            ):
                return None
            payload = EventRow.envelope["payload"]
            first = session.scalar(
                select(EventRow)
                .where(
                    EventRow.run_issuer == intent.run_issuer,
                    EventRow.run_id == intent.run_id,
                    EventRow.envelope["task_ref"]["issuer_domain"].as_string() == intent.task_issuer,
                    EventRow.envelope["task_ref"]["id"].as_string() == intent.task_id,
                    payload["event_type"].as_string() == "action_outcome",
                    payload["action_key"].as_string() == action_key,
                    payload["outcome"].as_string() == ActionOutcome.SUCCEEDED.value,
                )
                .order_by(EventRow.id)
                .limit(1)
            )
            if first is None or first.envelope["payload"].get("result_digest") != outcome.result_digest:
                return None
            return first.received_at

    def action_result(self, action_key: str, task_ref: BackendRef) -> Any:
        """Read the real result of an action owned by this logical task."""
        with self._read_session() as session:
            row = session.execute(
                select(ActionOutcomeRow.result_payload)
                .join(ActionIntentRow, ActionIntentRow.action_key == ActionOutcomeRow.action_key)
                .where(
                    ActionIntentRow.action_key == action_key,
                    ActionIntentRow.task_issuer == task_ref.issuer_domain,
                    ActionIntentRow.task_id == task_ref.id,
                )
            ).first()
        return row[0] if row is not None else None

    def list_run_actions(self, run_ref: BackendRef) -> list[dict[str, Any]]:
        """Reconciliation view: every action of one run with its verdict.

        Includes the real result content/reference when recorded — the query
        the host's action-recovery API is built on.
        """

        with self._read_session() as session:
            rows = (
                session.execute(
                    select(ActionIntentRow)
                    .where(
                        ActionIntentRow.run_issuer == run_ref.issuer_domain,
                        ActionIntentRow.run_id == run_ref.id,
                    )
                    .order_by(ActionIntentRow.created_at)
                )
                .scalars()
                .all()
            )
            actions: list[dict[str, Any]] = []
            for row in rows:
                outcome = session.get(ActionOutcomeRow, row.action_key)
                recovery = self._recovery_of_rows(row.action_key, row, outcome)
                actions.append(
                    {
                        **recovery.to_dict(),
                        "active_claim": row.active,
                        "claim_token": row.claim_token,
                        "execution_id": row.execution_id,
                        "tool_call_id": row.tool_call_id,
                        "session_id": row.session_id,
                        "result_payload": outcome.result_payload if outcome is not None else None,
                    }
                )
        return actions

    def get_run_action(self, run_ref: BackendRef, action_key: str) -> dict[str, Any] | None:
        """Read-only: one run action's recorded intent, outcome and result.

        The evidence receipt re-reads what the ledger already persisted; it
        must not touch the claim slot, the outcome or the recovery state.
        The claim token and the engine session stay inside the store: a
        receipt needs the recorded facts and the recorded result, never the
        arbitration internals. ``None`` means the run has no such action —
        a foreign action is indistinguishable from an absent one.
        """

        if not action_key:
            return None
        with self._read_session() as session:
            row = session.execute(
                select(ActionIntentRow).where(
                    ActionIntentRow.action_key == action_key,
                    ActionIntentRow.run_issuer == run_ref.issuer_domain,
                    ActionIntentRow.run_id == run_ref.id,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            outcome = session.get(ActionOutcomeRow, row.action_key)
            recovery = self._recovery_of_rows(row.action_key, row, outcome)
            return {
                **recovery.to_dict(),
                "task_issuer": row.task_issuer,
                "task_id": row.task_id,
                "run_issuer": row.run_issuer,
                "run_id": row.run_id,
                "execution_id": row.execution_id,
                "tool_call_id": row.tool_call_id,
                "result_payload": outcome.result_payload if outcome is not None else None,
                "result_ref": outcome.result_ref if outcome is not None else None,
                "outcome_recorded_at": outcome.recorded_at if outcome is not None else None,
            }

    def list_accepted_actions(self, run_ref: BackendRef) -> list[dict[str, Any]]:
        """Read-only: the run's accepted decisions with their action identities.

        Each entry carries the decision step id, the accepted content digest
        and every action's key, tool and recorded arguments. The evidence
        receipt re-derives the dispatched identity from these facts and
        re-hashes the arguments; the recorded parameters themselves never
        leave the platform layer.
        """

        from hecate_durable.runtime_hook import SqlAcceptedDecisionStore

        from .models import AcceptedDecisionRow

        require_kind(run_ref, RefKind.RUN)
        with self._read_session() as session:
            rows = (
                session.execute(
                    select(AcceptedDecisionRow)
                    .where(
                        AcceptedDecisionRow.issuer_domain == run_ref.issuer_domain,
                        AcceptedDecisionRow.run_id == run_ref.id,
                    )
                    .order_by(AcceptedDecisionRow.accepted_at, AcceptedDecisionRow.decision_step_id)
                )
                .scalars()
                .all()
            )
            decisions: list[dict[str, Any]] = []
            for row in rows:
                # The same schema and full-record digest check used by replay
                # must succeed before a query calls this an accepted decision.
                record = SqlAcceptedDecisionStore._row_to_record(row)
                decisions.append(
                    {
                        "decision_step_id": record.decision_step_id,
                        "content_digest": record.content_digest,
                        "accepted_at": record.accepted_at,
                        "actions": [action.to_dict() for action in record.actions],
                    }
                )
        return decisions

    def read_events(self, run_ref: BackendRef, *, cursor: int = 0, limit: int = 100) -> EventPage:
        return self.events.read(run_ref, cursor=cursor, limit=limit)

    def submission_received_at(self, run_ref: BackendRef) -> str | None:
        """Return the original receipt time, independent of later Task updates."""
        with self._read_session() as session:
            return session.execute(
                select(SubmissionRow.created_at).where(
                    SubmissionRow.run_issuer == run_ref.issuer_domain,
                    SubmissionRow.run_id == run_ref.id,
                )
            ).scalar_one_or_none()

    def event_runs_for_task(self, task_ref: BackendRef) -> list[BackendRef]:
        """List historical event streams, including attempts no longer current."""
        from hecate_durable.storage.models import EventRow

        with self._read_session() as session:
            rows = session.execute(
                select(EventRow.run_issuer, EventRow.run_id)
                .where(
                    EventRow.envelope["task_ref"]["issuer_domain"].as_string() == task_ref.issuer_domain,
                    EventRow.envelope["task_ref"]["id"].as_string() == task_ref.id,
                )
                .distinct()
                .order_by(EventRow.run_issuer, EventRow.run_id)
            ).all()
        return [_run_ref(issuer, run_id) for issuer, run_id in rows]

    def emit_event(
        self,
        task_ref: BackendRef,
        run_ref: BackendRef,
        *,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one governance event to the outbox on its own transaction.

        The host-facing emission path for events that are not a side effect
        of a state write (e.g. ``run_terminal`` from a dispatcher): same
        sequence allocation, validation, and dedup rules as the in-transaction
        emissions, just on its own short-lived session.
        """

        with self._session() as session, session.begin():
            envelope = self.events.emit(
                session,
                task_ref=task_ref,
                run_ref=run_ref,
                event_type=event_type,
                payload=payload,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        return envelope.to_dict()

    def run_for_task(self, task_ref: BackendRef) -> BackendRef | None:
        """The run linked to a task at submission time, when known."""

        with self._read_session() as session:
            row = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
            if row is None or row.run_issuer is None or row.run_id is None:
                return None
            return _run_ref(row.run_issuer, row.run_id)

    def task_for_run(self, run_ref: BackendRef) -> BackendRef | None:
        """Reverse lookup: the task owning a run (host-facing query API)."""

        with self._read_session() as session:
            row = (
                session.execute(
                    select(TaskStateRow).where(
                        TaskStateRow.run_issuer == run_ref.issuer_domain,
                        TaskStateRow.run_id == run_ref.id,
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            return _task_ref(row.task_issuer, row.task_id)

    def submission_for_key(self, key: str) -> SubmissionAssociation | None:
        """Read the committed association for a stable submission identity."""
        with self._read_session() as session:
            row = session.get(SubmissionRow, key)
            return None if row is None else _association_of(row)

    def submission_for_run(self, run_ref: BackendRef) -> SubmissionAssociation | None:
        """Return the idempotent submission that created a backend run."""

        with self._read_session() as session:
            row = (
                session.execute(
                    select(SubmissionRow).where(
                        SubmissionRow.run_issuer == run_ref.issuer_domain,
                        SubmissionRow.run_id == run_ref.id,
                    )
                )
                .scalars()
                .first()
            )
            return None if row is None else _association_of(row)
