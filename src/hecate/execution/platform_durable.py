"""PostgreSQL adapters for the durable-execution seams (platform side).

Worktree-B production implementation of ``DurableTaskStore`` and
``ControlCommandRecorder`` over the platform tables created in this
change (``task_lifecycle_states``, ``task_submissions``,
``control_commands``). Registered into the parameterized contract suite
(``tests/test_execution/conftest.py``) so every contract assertion runs
against this implementation unchanged.

Every method call opens its own short-lived session and transaction -
the plan's "one independent session per database transaction" rule. The
seam ABCs are synchronous, so these adapters ride a lazily-created
synchronous engine on the same database URL as the async application
engine (``postgresql+asyncpg`` / ``sqlite+aiosqlite`` URLs are mapped to
their sync drivers); async callers wrap calls in ``asyncio.to_thread``.
The sync engine can also be pointed at a separate URL (tests use a
shared in-memory SQLite database via ``StaticPool``).
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, cast

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
    validate_command_transition,
    validate_task_transition,
)
from hecate.contracts.execution.references import BackendRef
from hecate.core.database import Base
from hecate.execution.durable import ControlCommandRecorder, DurableTaskStore
from hecate.models.control_command import ControlCommandModel
from hecate.models.task_lifecycle import TaskLifecycleStateModel, TaskSubmissionModel

_SYNC_DRIVER_MAP = {
    "postgresql+asyncpg": "postgresql+psycopg",
    "sqlite+aiosqlite": "sqlite",
    "postgresql": "postgresql+psycopg",
    "postgresql+psycopg": "postgresql+psycopg",
    "sqlite": "sqlite",
}


def to_sync_database_url(url: str) -> str:
    """Map an async (or sync) database URL onto its sync driver form.

    PostgreSQL maps to the psycopg (v3) driver — the same package the
    ``redis`` extra already declares — because asyncpg has no sync API.
    Engine creation raises a clear error when psycopg is not installed;
    the stub binding keeps driver-less installs fully functional.
    """

    scheme = url.split("://", 1)[0]
    mapped = _SYNC_DRIVER_MAP.get(scheme, scheme)
    if "://" in url:
        return mapped + "://" + url.split("://", 1)[1]
    return mapped


def create_sync_engine(url: str) -> Engine:
    """Create the seam adapters' synchronous engine.

    In-memory SQLite uses ``StaticPool`` so every short session sees the
    same underlying connection (otherwise each session would get a fresh
    empty database).
    """

    sync_url = to_sync_database_url(url)
    if sync_url.startswith("sqlite") and ("://:" in sync_url or sync_url.endswith("://")):
        return create_engine(
            sync_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if sync_url.startswith("postgresql"):
        try:
            import psycopg  # noqa: F401
        except ImportError as err:
            raise ImportError(
                "the postgres durable binding requires the psycopg driver; "
                "install it with: uv pip install 'psycopg[binary]>=3.1.0'"
            ) from err
    return create_engine(sync_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


class _SyncTableMixin:
    """Shared session-per-call plumbing for the table-backed adapters."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)


class PostgresDurableTaskStore(_SyncTableMixin, DurableTaskStore):
    """Task lifecycle records and submission associations on platform tables."""

    def record_submission(
        self, key: IdempotencyKey, task_ref: BackendRef, run_ref: BackendRef
    ) -> SubmissionAssociation:
        with self._session_factory() as session:
            with session.begin():
                existing = session.execute(
                    select(TaskSubmissionModel).where(TaskSubmissionModel.idempotency_key == key.key)
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.request_digest != key.request_digest:
                        raise IdempotencyConflictError(key.key, existing.request_digest)
                    return SubmissionAssociation(
                        key=IdempotencyKey(
                            key=existing.idempotency_key,
                            subject=existing.subject,
                            workspace=existing.workspace,
                            request_digest=existing.request_digest,
                        ),
                        task_ref=BackendRef.from_dict(
                            {"kind": "task", "issuer_domain": existing.task_issuer_domain, "id": existing.task_ref_id}
                        ),
                        run_ref=BackendRef.from_dict(
                            {"kind": "run", "issuer_domain": existing.run_issuer_domain, "id": existing.run_ref_id}
                        ),
                    )
                row = TaskSubmissionModel(
                    idempotency_key=key.key,
                    subject=key.subject,
                    workspace=key.workspace,
                    request_digest=key.request_digest,
                    task_issuer_domain=task_ref.issuer_domain,
                    task_ref_id=task_ref.id,
                    run_issuer_domain=run_ref.issuer_domain,
                    run_ref_id=run_ref.id,
                )
                session.add(row)
            return SubmissionAssociation(key=key, task_ref=task_ref, run_ref=run_ref)

    def apply_task_state(
        self,
        task_ref: BackendRef,
        target: TaskLifecycleState,
        *,
        expected_revision: int | None = None,
        reconciled: bool = False,
        recorded_at: str = "",
    ) -> TaskStateRecord:
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(TaskLifecycleStateModel)
                .where(
                    TaskLifecycleStateModel.issuer_domain == task_ref.issuer_domain,
                    TaskLifecycleStateModel.task_ref_id == task_ref.id,
                    TaskLifecycleStateModel.deleted.is_(False),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is not None:
                current = TaskLifecycleState(row.lifecycle_state)
                validate_task_transition(current, target, reconciled=reconciled)
                if expected_revision is not None and expected_revision != row.revision:
                    raise ValueError(f"stale revision: expected {expected_revision}, current is {row.revision}")
                revision = row.revision + 1
                row.lifecycle_state = target.value
                row.revision = revision
                row.recorded_at = recorded_at or _utc_now_iso()
                row.writer_source = "platform"
                workspace_id = row.workspace_id
            else:
                if target is not TaskLifecycleState.QUEUED:
                    raise ValueError(f"first recorded state must be queued, got {target.value}")
                if expected_revision is not None and expected_revision != 0:
                    raise ValueError(f"stale revision: expected {expected_revision}, current is 0")
                revision = 0
                row = TaskLifecycleStateModel(
                    issuer_domain=task_ref.issuer_domain,
                    task_ref_id=task_ref.id,
                    lifecycle_state=target.value,
                    revision=revision,
                    recorded_at=recorded_at or _utc_now_iso(),
                    writer_source="platform",
                )
                session.add(row)
                session.flush()
                workspace_id = row.workspace_id
        return TaskStateRecord(
            task_ref=task_ref,
            lifecycle_state=target,
            revision=revision,
            recorded_at=recorded_at or row.recorded_at,
            writer_source="platform",
            extra={"workspace_id": str(workspace_id)} if workspace_id is not None else {},
        )

    def get_task_state(self, task_ref: BackendRef) -> TaskStateRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(TaskLifecycleStateModel).where(
                    TaskLifecycleStateModel.issuer_domain == task_ref.issuer_domain,
                    TaskLifecycleStateModel.task_ref_id == task_ref.id,
                    TaskLifecycleStateModel.deleted.is_(False),
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            extra: dict[str, Any] = {}
            if row.workspace_id is not None:
                extra["workspace_id"] = str(row.workspace_id)
            return TaskStateRecord(
                task_ref=task_ref,
                lifecycle_state=TaskLifecycleState(row.lifecycle_state),
                revision=row.revision,
                recorded_at=row.recorded_at,
                writer_source=row.writer_source,
                extra=extra,
            )

    # --- platform-side helpers (not part of the seam ABC) -------------------

    def attach_workspace(self, task_ref: BackendRef, workspace_id: uuid.UUID) -> None:
        """Attribute one lifecycle row to a workspace (scoped queries).

        Called right after the task/run registration transaction so
        workspace-scoped reconciliation queries can find the row. The
        seam itself stays workspace-agnostic (contract refs only).
        """

        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(TaskLifecycleStateModel)
                .where(
                    TaskLifecycleStateModel.issuer_domain == task_ref.issuer_domain,
                    TaskLifecycleStateModel.task_ref_id == task_ref.id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is not None and row.workspace_id is None:
                row.workspace_id = workspace_id


class PostgresControlCommandRecorder(_SyncTableMixin, ControlCommandRecorder):
    """Command receipt records on the ``control_commands`` table."""

    def record(self, command: ControlCommandRecord) -> ControlCommandRecord:
        stored_extra = command.to_dict()
        with self._session_factory() as session, session.begin():
            existing = session.execute(
                select(ControlCommandModel).where(
                    ControlCommandModel.command_id == command.command_id,
                    ControlCommandModel.deleted.is_(False),
                )
            ).scalar_one_or_none()
            if existing is not None:
                replay = self._to_record(existing)
                if replay.to_dict() != stored_extra:
                    raise ValueError(f"command {command.command_id!r} already recorded with different content")
                return replay
            session.add(
                ControlCommandModel(
                    command_id=command.command_id,
                    kind=command.kind.value,
                    issuer=command.issuer,
                    task_issuer_domain=command.task_ref.issuer_domain,
                    task_ref_id=command.task_ref.id,
                    run_issuer_domain=command.run_ref.issuer_domain if command.run_ref else None,
                    run_ref_id=command.run_ref.id if command.run_ref else None,
                    issued_at=command.issued_at,
                    state=command.state.value,
                    expires_at=command.expires_at,
                    expected_revision=command.expected_revision,
                    payload=dict(command.payload),
                    payload_schema_ref=command.payload_schema_ref,
                    detail_ns=dict(command.detail_ns),
                )
            )
        return command

    def transition(self, command_id: str, target: CommandState) -> ControlCommandRecord:
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(ControlCommandModel)
                .where(
                    ControlCommandModel.command_id == command_id,
                    ControlCommandModel.deleted.is_(False),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"unknown command {command_id!r}")
            validate_command_transition(CommandState(row.state), target)
            row.state = target.value
            session.flush()
            return self._to_record(row)

    def get(self, command_id: str) -> ControlCommandRecord | None:
        with self._session_factory() as session:
            row = session.execute(
                select(ControlCommandModel).where(
                    ControlCommandModel.command_id == command_id,
                    ControlCommandModel.deleted.is_(False),
                )
            ).scalar_one_or_none()
            return self._to_record(row) if row is not None else None

    def _to_record(self, row: ControlCommandModel) -> ControlCommandRecord:
        return ControlCommandRecord(
            command_id=row.command_id,
            kind=ControlCommandKind(row.kind),
            issuer=row.issuer,
            task_ref=BackendRef.from_dict(
                {"kind": "task", "issuer_domain": row.task_issuer_domain, "id": row.task_ref_id}
            ),
            issued_at=row.issued_at,
            state=CommandState(row.state),
            run_ref=(
                BackendRef.from_dict({"kind": "run", "issuer_domain": row.run_issuer_domain, "id": row.run_ref_id})
                if row.run_issuer_domain and row.run_ref_id
                else None
            ),
            expires_at=row.expires_at,
            expected_revision=row.expected_revision,
            payload=dict(row.payload),
            payload_schema_ref=row.payload_schema_ref,
            detail_ns=dict(row.detail_ns),
        )


def _kind_from(value: str):
    from hecate.contracts.execution.durable import ControlCommandKind

    return ControlCommandKind(value)


class PlatformDurableFactory:
    """Thread-safe lazy holder for the process-wide sync engine."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = create_sync_engine(self._url)
            return self._engine

    def task_store(self) -> PostgresDurableTaskStore:
        return PostgresDurableTaskStore(self.engine())

    def command_recorder(self) -> PostgresControlCommandRecorder:
        return PostgresControlCommandRecorder(self.engine())

    def create_all(self) -> None:
        """Create the seam tables (test/dev bootstrap on a fresh database)."""

        from sqlalchemy import Table

        tables = [
            cast(Table, TaskLifecycleStateModel.__table__),
            cast(Table, TaskSubmissionModel.__table__),
            cast(Table, ControlCommandModel.__table__),
        ]
        Base.metadata.create_all(self.engine(), tables=tables)
