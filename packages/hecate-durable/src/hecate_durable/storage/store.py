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

from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hecate_durable.contracts.durable import (
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
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.seams import ActionLedger, ControlCommandRecorder, DurableTaskStore
from hecate_durable.storage.eventlog import EventPage, SqlEventLog
from hecate_durable.storage.lease import LeaseHandle, LeaseManager
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
        self.events = SqlEventLog(self._session_factory, source=source, actor_id=actor_id, clock=self._clock)
        self.leases = LeaseManager(self._session_factory, clock=lease_clock)

    @staticmethod
    def _build_engine(url: str):
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

        @event.listens_for(engine, "begin")
        def _sqlite_begin_immediate(connection):  # noqa: ANN001
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        return engine

    # -- schema / lifecycle ---------------------------------------------------

    def create_schema(self) -> None:
        """Create this package's tables (standalone-host startup path)."""

        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()

    def _session(self) -> Session:
        return self._session_factory()

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
    ) -> SubmissionAssociation:
        """Idempotent submission that also creates the task and its linkage.

        Same key + same digest returns the original association without
        creating anything twice; the task row (``queued``, revision 0), run
        linkage, replay input, and the ``task_submitted`` governance event all
        commit in one transaction.
        """

        for attempt in range(3):
            try:
                with self._session() as session, session.begin():
                    existing = session.get(SubmissionRow, key.key)
                    if existing is not None:
                        self._check_submission_digest(key, existing)
                        return _association_of(existing)
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
    ) -> TaskStateRecord:
        recorded = recorded_at or self._clock()
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
            if applied_command_id is not None:
                command = session.execute(
                    select(CommandRow).where(CommandRow.command_id == applied_command_id).with_for_update()
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
                    self.events.emit(
                        session,
                        task_ref=task_ref,
                        run_ref=run_ref,
                        event_type="command_state",
                        payload={"command_id": applied_command_id, "state": CommandState.APPLIED.value},
                    )
        return record

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

    def get_task_state(self, task_ref: BackendRef) -> TaskStateRecord | None:
        with self._session() as session:
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

        with self._session() as session:
            row = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
            return None if row is None else row.input_payload

    def list_tasks(self, states: set[TaskLifecycleState] | None = None) -> list[TaskStateRecord]:
        """All task records, optionally filtered by lifecycle state."""

        with self._session() as session:
            rows = session.execute(select(TaskStateRow).order_by(TaskStateRow.created_at)).scalars().all()
        records = [_task_record_of(row) for row in rows]
        if states is None:
            return records
        return [record for record in records if record.lifecycle_state in states]

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
            row.state = target.value
            row.updated_at = self._clock()
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
        with self._session() as session:
            row = session.get(CommandRow, command_id)
            return None if row is None else _command_of(row)

    def list_pending_commands(self, workspace_id: str) -> list[ControlCommandRecord]:
        """Workspace-scoped ``requested`` receipts (lazy-expiry sweep input)."""

        with self._session() as session:
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
    ) -> None:
        """Record the real outcome; ``claim_token`` enables late-receipt fencing.

        With a token the write is a conditional UPDATE on the claim slot: a
        stale token (another executor re-claimed after a lease expiry) is
        rejected — the authoritative outcome stays untouched and the late
        receipt is journaled as an event for reconciliation. Without a token
        this is the seam-level single-writer write.
        """

        if claim_token is not None:
            self._record_outcome_fenced(outcome, claim_token=claim_token, result_payload=result_payload)
            return
        with self._session() as session, session.begin():
            intent = session.execute(
                select(ActionIntentRow).where(ActionIntentRow.action_key == outcome.action_key).with_for_update()
            ).scalar_one_or_none()
            if intent is None:
                raise KeyError(f"action {outcome.action_key!r} has no recorded intent")
            self._write_outcome(session, intent, outcome, result_payload)

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
        with self._session() as session:
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

    def list_run_actions(self, run_ref: BackendRef) -> list[dict[str, Any]]:
        """Reconciliation view: every action of one run with its verdict.

        Includes the real result content/reference when recorded — the query
        the host's action-recovery API is built on.
        """

        with self._session() as session:
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

    def read_events(self, run_ref: BackendRef, *, cursor: int = 0, limit: int = 100) -> EventPage:
        return self.events.read(run_ref, cursor=cursor, limit=limit)

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

        with self._session() as session:
            row = session.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
            if row is None or row.run_issuer is None or row.run_id is None:
                return None
            return _run_ref(row.run_issuer, row.run_id)

    def task_for_run(self, run_ref: BackendRef) -> BackendRef | None:
        """Reverse lookup: the task owning a run (host-facing query API)."""

        with self._session() as session:
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
