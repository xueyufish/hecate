"""Task control service tests (step6 worker change).

The service spans two engines by design — the async application ORM and
the synchronous durable-seam store — so these tests run both on one
file-backed SQLite database. Dispatch goes through the real durable
worker + dispatcher chain with the entry-service boundary stubbed (the
entry chain has its own suites); idempotent submission, cancel
arbitration, command receipts, durable waits, cursor pagination, and
reconciliation run for real.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.core.database import Base
from hecate.execution.governance_events import RUN_TERMINAL, TASK_SUBMITTED
from hecate.execution.task_control import (
    SubmissionConflictError,
    TaskControlService,
    task_ref_of,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

# Isolated per-test identifiers (the file DB is per-test, but explicit
# constants keep cross-workspace assertions readable).
WS = uuid.UUID("00000000-0000-0000-0000-000000000010")
OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000011")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000012")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000013")


class _EntryStub:
    """Patches the dispatcher's entry boundary; records calls, canned reply."""

    def __init__(self, content: str = "stub reply") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content
        self.gate: asyncio.Event | None = None
        self._real: Any = None

    def patch(self) -> None:
        import hecate.execution.task_dispatcher as dispatcher_module

        self._real = dispatcher_module.PlatformTaskDispatcher._execute
        dispatcher_module.PlatformTaskDispatcher._execute = _execute_patch(self)

    def unpatch(self) -> None:
        import hecate.execution.task_dispatcher as dispatcher_module

        dispatcher_module.PlatformTaskDispatcher._execute = self._real

    async def __call__(self, db, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        return {"status": "succeeded", "content": self.content}


def _execute_patch(stub: _EntryStub):
    async def _execute(self, db, context, **kwargs: Any) -> dict[str, Any]:
        return await stub(db, **kwargs)

    return _execute


class _GatedEntry(_EntryStub):
    """Entry boundary that blocks until released (running-state tests)."""

    def __init__(self) -> None:
        super().__init__("late reply")
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def __call__(self, db, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        self.started.set()
        await self.gate.wait()
        return {"status": "succeeded", "content": self.content}


@pytest.fixture
async def harness(tmp_path):
    """File-backed SQLite with both engines plus seeded tenant and agent."""

    db_file = tmp_path / "task_control.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    # WAL lets the async sessions' open read transactions coexist with the
    # seam store's independent write transactions on the same file.
    @event.listens_for(async_engine.sync_engine, "connect")
    def _wal_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    from hecate_durable.storage import SqlDurableStore

    from hecate.core.composition.durable_platform import to_sync_database_url

    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()

    @event.listens_for(store.engine, "connect")
    def _wal_sync(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.core.composition.durable_platform import DurableSuite

    suite = DurableSuite(
        store=store,
        recorder=store,
        ledger=store,
        backend="postgres",
        ledger_source="core",
    )

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(WorkspaceModel(id=OTHER_WS, org_id=ORG, name="other", slug=f"ws-{OTHER_WS.hex}"))
        db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="task-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="a" * 64
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=WS,
                organization_id=ORG,
                owner_user_id=OWNER,
            )
        )
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=WS,
                backend_type=BackendType.BUILTIN,
                access_mode=AccessMode.IN_PROCESS,
                issuer_domain="hecate",
                capability_snapshot={},
                axes_harness="hecate",
                axes_environment="none",
                axes_tool_execution="hecate_gateway",
                is_default=True,
            )
        )
        await db.commit()
        agent_id = agent.id

    yield {
        "session_factory": session_factory,
        "suite": suite,
        "agent_id": agent_id,
        "engine": async_engine,
        "store": store,
        "sync_url": to_sync_database_url(f"sqlite+aiosqlite:///{db_file}"),
    }

    await async_engine.dispose()
    store.dispose()


def _dispatcher(harness) -> Any:
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    return PlatformTaskDispatcher(harness["store"], harness["session_factory"])


def _service(harness, db: AsyncSession, *, worker: Any | None = None) -> TaskControlService:
    return TaskControlService(
        db,
        store=harness["suite"].store,
        recorder=harness["suite"].recorder,
        backend="postgres",
        ledger_source="core",
        session_factory=harness["session_factory"],
        dispatcher=None if worker is not None else _dispatcher(harness),
        worker=worker,
    )


async def _submit(
    harness,
    db: AsyncSession,
    service: TaskControlService | None = None,
    **overrides: Any,
):
    svc = service or _service(harness, db)
    kwargs: dict[str, Any] = dict(
        workspace_id=WS,
        user_id=OWNER,
        goal="summarize the inventory",
        agent_id=harness["agent_id"],
        input={"messages": [{"role": "user", "content": "summarize"}]},
    )
    kwargs.update(overrides)
    return await svc.submit(**kwargs)


async def test_submit_wait_reaches_terminal_via_worker(harness):
    stub = _EntryStub("final answer")
    stub.patch()
    try:
        from hecate_durable.worker import DurableWorker

        worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
        async with harness["session_factory"]() as db:
            result = await _submit(harness, db, _service(harness, db, worker=worker), wait=True)
            assert result.lifecycle_state == "succeeded"

        task_uuid = uuid.UUID(result.task_ref.id)
        async with harness["session_factory"]() as db:
            detail = await _service(harness, db).get_task_detail(WS, task_uuid)
            assert detail["lifecycle_state"] == "succeeded"
            assert len(detail["runs"]) == 1
            assert detail["runs"][0]["projection"]["state"] == "succeeded"

            page = await _service(harness, db).read_run_events(WS, uuid.UUID(result.run_ref.id))
            assert page.events[-1].payload_schema_ref == RUN_TERMINAL
            sequences = [e.source_sequence for e in page.events]
            assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
            for envelope in page.events:
                assert envelope.actor is not None and envelope.source is not None
    finally:
        stub.unpatch()


async def test_submit_idempotent_replay_returns_original(harness):
    stub = _EntryStub()
    stub.patch()
    try:
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            first = await _submit(harness, db, service, idempotency_key="key-1", wait=True)
            second = await _submit(harness, db, service, idempotency_key="key-1", wait=True)
            assert second.replayed is True
            assert second.task_ref == first.task_ref
            assert second.run_ref == first.run_ref

            detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(first.task_ref.id))
            assert len(detail["runs"]) == 1  # no second execution minted
    finally:
        stub.unpatch()


async def test_submit_conflicting_replay_rejected(harness):
    stub = _EntryStub()
    stub.patch()
    try:
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            await _submit(harness, db, service, idempotency_key="key-2", goal="first goal", wait=True)
            with pytest.raises(SubmissionConflictError):
                await _submit(harness, db, service, idempotency_key="key-2", goal="different goal", wait=True)
    finally:
        stub.unpatch()


async def test_background_dispatch_via_worker_reconciles(harness, monkeypatch):
    """A queued task survives the submission process: a second worker picks it up.

    The submission-side background trigger is held (simulating a crash right
    after submit); a separate worker's reconcile must take over.
    """

    stub = _EntryStub("async reply")
    stub.patch()
    held: list[Any] = []

    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", held.append)
    try:
        async with harness["session_factory"]() as db:
            result = await _submit(harness, db)  # queued; trigger held
            assert result.lifecycle_state == "queued"
        assert held, "background trigger must have been scheduled"

        from hecate_durable.worker import DurableWorker

        reconciler = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
        assert await reconciler.reconcile() == 1

        async with harness["session_factory"]() as db:
            detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(result.task_ref.id))
            assert detail["lifecycle_state"] == "succeeded"
            assert len(stub.calls) == 1
    finally:
        stub.unpatch()


async def test_cancel_running_stays_requested(harness):
    gated = _GatedEntry()
    gated.patch()

    try:
        from hecate_durable.worker import DurableWorker

        worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
        async with harness["session_factory"]() as db:
            service = _service(harness, db, worker=worker)
            result = await _submit(harness, db, service)  # dispatch via worker
            await gated.started.wait()

            issued = await _service(harness, db).issue_command(
                workspace_id=WS,
                task_id=uuid.UUID(result.task_ref.id),
                kind=ControlCommandKind.CANCEL,
                issuer=str(OWNER),
            )
            # HTTP-visible success is only a recorded request — never applied.
            assert issued.record.state is CommandState.REQUESTED
            assert "no cooperative abort channel" in (issued.detail or "")

            gated.gate.set()
        deadline = asyncio.get_running_loop().time() + 10
        state = "running"
        while asyncio.get_running_loop().time() < deadline:
            async with harness["session_factory"]() as db:
                detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(result.task_ref.id))
                state = detail["lifecycle_state"]
            if state in ("succeeded", "failed", "cancelled"):
                break
            await asyncio.sleep(0.05)
        assert state == "succeeded"
    finally:
        gated.unpatch()


async def test_cancel_queued_applies_without_execution(harness, monkeypatch):
    stub = _EntryStub("should not run")
    stub.patch()
    held: list[Any] = []
    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", held.append)
    try:
        async with harness["session_factory"]() as db:
            result = await _submit(harness, db)  # queued, no worker bound
            issued = await _service(harness, db).issue_command(
                workspace_id=WS,
                task_id=uuid.UUID(result.task_ref.id),
                kind=ControlCommandKind.CANCEL,
                issuer=str(OWNER),
            )
            # The CAS applied the cancel immediately — nothing was executing.
            assert issued.record.state is CommandState.APPLIED
            detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(result.task_ref.id))
            assert detail["lifecycle_state"] == "cancelled"
            assert stub.calls == []  # no execution ever started
    finally:
        stub.unpatch()


async def test_cancel_terminal_task_rejected(harness):
    stub = _EntryStub()
    stub.patch()
    try:
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            result = await _submit(harness, db, service, wait=True)
            issued = await _service(harness, db).issue_command(
                workspace_id=WS,
                task_id=uuid.UUID(result.task_ref.id),
                kind=ControlCommandKind.CANCEL,
                issuer=str(OWNER),
            )
            assert issued.record.state is CommandState.REJECTED
            assert "terminal" in (issued.detail or "")
    finally:
        stub.unpatch()


async def test_unsupported_command_kind_rejected(harness):
    stub = _EntryStub()
    stub.patch()
    try:
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            result = await _submit(harness, db, service, wait=True)
            issued = await _service(harness, db).issue_command(
                workspace_id=WS,
                task_id=uuid.UUID(result.task_ref.id),
                kind=ControlCommandKind.PAUSE,
                issuer=str(OWNER),
            )
            assert issued.record.state is CommandState.REJECTED
    finally:
        stub.unpatch()


async def test_expired_command_converges_on_read(harness, monkeypatch):
    gated = _GatedEntry()
    gated.patch()
    held: list[Any] = []
    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", held.append)
    try:
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            result = await _submit(harness, db, service)  # queued
            issued = await _service(harness, db).issue_command(
                workspace_id=WS,
                task_id=uuid.UUID(result.task_ref.id),
                kind=ControlCommandKind.CANCEL,
                issuer=str(OWNER),
                expires_at=past,
            )
            record = await _service(harness, db).get_command(WS, issued.record.command_id)
            assert record.state is CommandState.EXPIRED
            assert harness["store"].get_task_state(result.task_ref).lifecycle_state is TaskLifecycleState.QUEUED
    finally:
        gated.unpatch()


async def test_events_workspace_isolation(harness):
    from hecate.execution.task_control import TaskControlNotFoundError

    stub = _EntryStub()
    stub.patch()
    try:
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            result = await _submit(harness, db, service, wait=True)
            with pytest.raises(TaskControlNotFoundError):
                await _service(harness, db).read_run_events(OTHER_WS, uuid.UUID(result.run_ref.id))
    finally:
        stub.unpatch()


async def test_events_pagination_resumes_by_cursor(harness):
    stub = _EntryStub()
    stub.patch()
    try:
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            result = await _submit(harness, db, service, wait=True)
            run_id = uuid.UUID(result.run_ref.id)

            # All governance facts use the outbox. A one-event page must
            # resume through state changes and end at one terminal event.
            first = await service.read_run_events(WS, run_id, limit=1)
            assert len(first.events) == 1
            assert first.events[-1].payload_schema_ref == TASK_SUBMITTED
            second = await service.read_run_events(WS, run_id, cursor=first.next_cursor, limit=100)
            assert second.events[-1].payload_schema_ref == RUN_TERMINAL
            complete = [*first.events, *second.events]
            assert len({e.event_id for e in complete}) == len(complete)
            assert sum(e.payload_schema_ref == RUN_TERMINAL for e in complete) == 1
            assert list((await service.read_run_events(WS, run_id, cursor=second.next_cursor)).events) == []
    finally:
        stub.unpatch()


async def test_pending_reconciliation_lists_tasks_and_actions(harness):
    async with harness["session_factory"]() as db:
        result = await _submit(harness, db)  # queued, nothing will run it
        t_ref = task_ref_of(uuid.UUID(result.task_ref.id))
        # Park it in reconciliation the way the worker's retry budget does.
        await asyncio.to_thread(
            harness["store"].apply_task_state,
            t_ref,
            TaskLifecycleState.RECONCILIATION_REQUIRED,
            extra_update={"dispatch_attempts": 5, "last_error": "boom"},
        )

        view = await _service(harness, db).pending_reconciliation(WS)
        assert view["ledger_source"] == "core"
        entry = next(item for item in view["tasks"] if item["task_ref"]["id"] == t_ref.id)
        assert entry["dispatch_attempts"] == 5
        other_view = await _service(harness, db).pending_reconciliation(OTHER_WS)
        assert other_view["tasks"] == []


async def test_durable_wait_and_wake(harness):
    """An execution that raises the wait signal parks the task; the token wakes it."""

    import hecate.execution.task_dispatcher as dispatcher_module
    from hecate.contracts.execution.durable import ControlCommandKind as Kind

    real = dispatcher_module.PlatformTaskDispatcher._execute

    async def waiting_execute(self, db, context, **kwargs):
        raise dispatcher_module.TaskWaitingSignalError(
            Kind.PROVIDE_INPUT, {"reason": "missing parameter"}, expires_in_seconds=3600
        )

    dispatcher_module.PlatformTaskDispatcher._execute = waiting_execute
    try:
        from hecate_durable.worker import DurableWorker

        worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
        async with harness["session_factory"]() as db:
            result = await _submit(harness, db, _service(harness, db, worker=worker), wait=True)
            assert result.lifecycle_state == "waiting_input"

            task_uuid = uuid.UUID(result.task_ref.id)
            detail = await _service(harness, db).get_task_detail(WS, task_uuid)
            wait = detail["wait"]
            assert wait["wake_kind"] == "provide_input"
            assert wait["consumed"] is False

            # Worker-deployment wiring: the wake requeues only; the durable
            # worker's cycle performs the re-dispatch (the inline spawn is
            # exercised by the workflow-callback API tests).
            # Wrong token is rejected.
            wrong = await _service(harness, db, worker=worker).issue_command(
                workspace_id=WS,
                task_id=task_uuid,
                kind=Kind.PROVIDE_INPUT,
                issuer=str(OWNER),
                detail_ns={"wait_token": "bogus"},
            )
            assert wrong.record.state is CommandState.REJECTED

            # The correct token wakes and requeues the task.
            ok = await _service(harness, db, worker=worker).issue_command(
                workspace_id=WS,
                task_id=task_uuid,
                kind=Kind.PROVIDE_INPUT,
                issuer=str(OWNER),
                detail_ns={"wait_token": wait["wait_token"]},
                payload={"answer": 42},
                payload_schema_ref="test:provide-input/0",
            )
            assert ok.record.state is CommandState.APPLIED
            detail = await _service(harness, db).get_task_detail(WS, task_uuid)
            assert detail["lifecycle_state"] == "queued"
            assert detail["wait"]["consumed"] is True

            # Token reuse is rejected.
            reuse = await _service(harness, db, worker=worker).issue_command(
                workspace_id=WS,
                task_id=task_uuid,
                kind=Kind.PROVIDE_INPUT,
                issuer=str(OWNER),
                detail_ns={"wait_token": wait["wait_token"]},
            )
            assert reuse.record.state is CommandState.REJECTED
    finally:
        dispatcher_module.PlatformTaskDispatcher._execute = real


async def test_committed_submission_recovers_missing_platform_records(harness, monkeypatch) -> None:
    """Crash after durable submit retains the original IDs and frozen identity."""
    from hecate_durable.worker import DurableWorker

    import hecate.execution.task_control as task_control_module
    import hecate.execution.task_registration as registration

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())
    real = registration.ensure_submission_registration

    async def crash(*args, **kwargs):
        raise RuntimeError("crash after durable commit")

    monkeypatch.setattr(registration, "ensure_submission_registration", crash)
    async with harness["session_factory"]() as db:
        with pytest.raises(RuntimeError, match="crash after durable commit"):
            await _submit(harness, db, idempotency_key="crash-registration")
    monkeypatch.setattr(registration, "ensure_submission_registration", real)
    records = harness["store"].list_tasks()
    assert len(records) == 1
    task_ref = records[0].task_ref
    original = harness["store"].get_task_input(task_ref)
    stub = _EntryStub()
    stub.patch()
    try:
        worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
        assert await worker.reconcile() == 1
        async with harness["session_factory"]() as db:
            service = _service(harness, db)
            replay = await _submit(harness, db, service, idempotency_key="crash-registration")
            assert replay.task_ref == task_ref
            assert replay.run_ref.id == original["platform_run_id"]
            detail = await service.get_task_detail(WS, uuid.UUID(task_ref.id))
            assert len(detail["runs"]) == 1
    finally:
        stub.unpatch()


async def test_stale_expected_revision_rejects_cancel_without_effect(harness, monkeypatch) -> None:
    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())
    async with harness["session_factory"]() as db:
        service = _service(harness, db)
        result = await _submit(harness, db, service)
        harness["store"].apply_task_state(result.task_ref, TaskLifecycleState.RUNNING, expected_revision=0)
        harness["store"].apply_task_state(result.task_ref, TaskLifecycleState.WAITING_INPUT, expected_revision=1)
        issued = await service.issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(result.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer=str(OWNER),
            expected_revision=0,
        )
        assert issued.record.state is CommandState.REJECTED
        assert harness["store"].get_task_state(result.task_ref).lifecycle_state is TaskLifecycleState.WAITING_INPUT
