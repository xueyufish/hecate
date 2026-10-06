"""Double-master and orchestration integration tests (step6 worker change).

Two concerns that span the scheduler, the durable worker, and the task
control surface:

- **Double-master verification** — the scheduler's job records and the
  platform task lifecycle have disjoint single writers (static assertion),
  and the fencing story holds at runtime: a stale executor's late terminal
  write is rejected by the revision CAS while the takeover's write lands.

- **Minimal orchestration scenario** — a deterministic workflow executor
  starts an agent child task through the platform Task/Run interface and
  parks itself in a durable wait keyed to the child's task ref; the child's
  terminal event wakes it. No delegation/acceptance semantics (step13).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from hecate_durable.contracts.durable import InvalidTaskTransitionError
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    TaskLifecycleState,
)
from hecate.core.database import Base
from hecate.execution.task_control import TaskControlService, task_ref_of
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

WS = uuid.UUID("00000000-0000-0000-0000-0000000000a0")
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


# Imported at module scope so the scheduler models register in Base.metadata
# before the session-scoped create_all runs (the autouse row-clearing fixture
# must see every table it deletes from).
import hecate.execution.task_control as _tc_module  # noqa: E402, F401
import hecate.ops.scheduling.manager as _manager_module  # noqa: E402, F401


def test_scheduler_and_task_control_have_disjoint_writers() -> None:
    """Static single-writer assertion: neither side imports the other's store."""

    import inspect

    manager_src = inspect.getsource(_manager_module)
    assert "DurableTaskStore" not in manager_src and "apply_task_state" not in manager_src, (
        "the scheduler must not write the task lifecycle projection (double-master)"
    )
    tc_src = inspect.getsource(_tc_module)
    assert "ScheduledTask" not in tc_src and "scheduled_task_executions" not in tc_src, (
        "task control must not write the scheduler's job records (double-master)"
    )


async def test_stale_executor_terminal_write_is_fenced(harness, monkeypatch) -> None:
    """Old ownership after a lease takeover cannot overwrite the authority."""

    from hecate_durable.worker import DurableWorker

    import hecate.execution.task_control as task_control_module

    # Hold the submission-side trigger: this test drives every dispatch by hand.
    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())

    store = harness["store"]

    async with harness["session_factory"]() as db:
        result = await TaskControlService(
            db,
            store=store,
            recorder=store,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        ).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="fencing test",
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "go"}]},
        )
    t_ref = task_ref_of(uuid.UUID(result.task_ref.id))

    worker_b = DurableWorker(store, _noop_dispatcher(store), leases=store.leases, worker_id="worker-b")

    # A claims and starts executing (worker-a marks running, then stalls).
    record = await asyncio.to_thread(store.get_task_state, t_ref)
    await asyncio.to_thread(
        store.apply_task_state, t_ref, TaskLifecycleState.RUNNING, expected_revision=record.revision
    )
    # The lease expires without a renewal (worker-a "partitioned").
    harness["clock"].advance(3600)

    # B takes over: its reconcile cycle sees RUNNING under an expired
    # lease, re-queues under a fresh fencing token, and dispatches.
    assert await worker_b.reconcile() == 1
    assert store.get_task_state(t_ref).lifecycle_state is TaskLifecycleState.SUCCEEDED

    # A wakes up and presents its stale terminal write: the revision CAS
    # rejects it — the authority is untouched.
    with pytest.raises((InvalidTaskTransitionError, ValueError)):
        await asyncio.to_thread(store.apply_task_state, t_ref, TaskLifecycleState.SUCCEEDED, expected_revision=1)
    assert store.get_task_state(t_ref).lifecycle_state is TaskLifecycleState.SUCCEEDED


def _noop_dispatcher(store):
    async def dispatch(task_ref, record, lease) -> None:
        await asyncio.to_thread(store.apply_task_state, task_ref, TaskLifecycleState.SUCCEEDED)

    return dispatch


async def test_orchestration_child_wait_and_wake(harness, monkeypatch):
    """Workflow executor submits a child task, waits durably, child wakes it."""

    from hecate_durable.worker import DurableWorker

    import hecate.execution.task_control as task_control_module
    import hecate.execution.task_dispatcher as dispatcher_module

    # Hold the submission-side trigger: workers dispatch explicitly below.
    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())

    store = harness["store"]

    async with harness["session_factory"]() as db:
        parent_service = TaskControlService(
            db,
            store=store,
            recorder=store,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )

        # Step 1: the workflow starts a child task through the platform
        # Task/Run interface (same submit surface, internal actor).
        child = await parent_service.submit(
            workspace_id=WS,
            user_id=None,  # system-initiated
            goal="child step",
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "child"}]},
        )
        child_ref = child.task_ref

        # Step 2: the workflow (here: any execution wrapper) parks in a
        # durable wait keyed to the child's task ref. The parent goes
        # through the real submit path with the wait-raising executor.
        real_execute = dispatcher_module.PlatformTaskDispatcher._execute

        async def wait_for_child(self, db, context, **kwargs):
            raise dispatcher_module.TaskWaitingSignalError(
                ControlCommandKind.PROVIDE_INPUT,
                {"await_task_ref": child_ref.to_dict()},
                expires_in_seconds=600,
            )

        monkeypatch.setattr(dispatcher_module.PlatformTaskDispatcher, "_execute", wait_for_child)
        try:
            parent = await parent_service.submit(
                workspace_id=WS,
                user_id=None,  # system-initiated
                goal="parent workflow",
                agent_id=harness["agent_id"],
                input={"messages": [{"role": "user", "content": "run parent workflow"}]},
                wait=True,
            )
        finally:
            monkeypatch.setattr(dispatcher_module.PlatformTaskDispatcher, "_execute", real_execute, raising=False)

        parent_task_id = uuid.UUID(parent.task_ref.id)
        parent_ref = parent.task_ref
        assert parent.lifecycle_state == "waiting_input"
        assert store.get_task_state(parent_ref).lifecycle_state is TaskLifecycleState.WAITING_INPUT

        # Step 3: the child completes (worker executes it to terminal).
        stub_done = asyncio.Event()

        async def child_completes(self, db, context, **kwargs):
            stub_done.set()
            return {"status": "succeeded", "content": "child done"}

        monkeypatch.setattr(dispatcher_module.PlatformTaskDispatcher, "_execute", child_completes)
        try:
            worker = DurableWorker(store, _dispatcher_of(harness), leases=store.leases)
            assert await worker.dispatch_once(child_ref) is True
            assert store.get_task_state(child_ref).lifecycle_state is TaskLifecycleState.SUCCEEDED
        finally:
            monkeypatch.setattr(dispatcher_module.PlatformTaskDispatcher, "_execute", real_execute, raising=False)

        # Step 4: the workflow is woken with the child's outcome in the
        # persisted input; the wake token is single-use.
        detail = await parent_service.get_task_detail(WS, parent_task_id)
        wait = detail["wait"]
        assert wait["contract_ref"]["await_task_ref"]["id"] == child_ref.id
        wake = await parent_service.issue_command(
            workspace_id=WS,
            task_id=parent_task_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer="workflow-engine",
            payload={"child_outcome": "succeeded"},
            payload_schema_ref="test:child-outcome/0",
            detail_ns={"wait_token": wait["wait_token"]},
        )
        assert wake.record.state is CommandState.APPLIED
        assert store.get_task_state(parent_ref).lifecycle_state is TaskLifecycleState.QUEUED


def _dispatcher_of(harness):
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    return PlatformTaskDispatcher(harness["store"], harness["session_factory"])


@pytest.fixture
async def harness(tmp_path: Path):
    db_file = tmp_path / "orchestration.db"
    dsn = f"sqlite:///{db_file}"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    @sa_event.listens_for(async_engine.sync_engine, "connect")
    def _wal_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(dsn, source="platform")

    @sa_event.listens_for(store.engine, "connect")
    def _wal_sync(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    store.create_schema()
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from datetime import UTC, datetime, timedelta

    class _Clock:
        now = datetime(2026, 1, 1, tzinfo=UTC)

        def advance(self, seconds: float) -> None:
            self.now = self.now + timedelta(seconds=seconds)

    clock = _Clock()
    import hecate_durable.storage.lease as lease_module

    original_default = lease_module._default_clock
    lease_module._default_clock = lambda: clock.now
    # The store builds its LeaseManager at __init__ with the real clock;
    # rebuild it with the test clock.
    store.leases = lease_module.LeaseManager(store.session_factory, clock=lambda: clock.now)

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="orch-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="c" * 64
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
        "store": store,
        "agent_id": agent_id,
        "clock": clock,
    }

    lease_module._default_clock = original_default
    await async_engine.dispose()
    store.dispose()


async def test_real_platform_dispatcher_rejects_late_success(harness, monkeypatch) -> None:
    """Exercise the production callback, rather than a CAS-aware test double."""
    from hecate_durable.worker import DurableWorker

    import hecate.execution.task_control as task_control_module
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())
    store = harness["store"]
    entered_a, entered_b = asyncio.Event(), asyncio.Event()
    finish_a, finish_b = asyncio.Event(), asyncio.Event()
    calls = 0

    async def execute(self, db, context, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered_a.set()
            await finish_a.wait()
            return {"status": "succeeded", "content": "stale"}
        entered_b.set()
        await finish_b.wait()
        return {"status": "succeeded", "content": "current"}

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", execute)
    async with harness["session_factory"]() as db:
        result = await TaskControlService(
            db,
            store=store,
            recorder=store,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        ).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="real fencing",
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "go"}]},
        )
    dispatcher = PlatformTaskDispatcher(store, harness["session_factory"])
    worker_a = DurableWorker(store, dispatcher, leases=store.leases, worker_id="a", lease_ttl=30)
    worker_b = DurableWorker(store, dispatcher, leases=store.leases, worker_id="b", lease_ttl=30)
    old = asyncio.create_task(worker_a.dispatch_once(result.task_ref))
    await asyncio.wait_for(entered_a.wait(), 5)
    harness["clock"].advance(31)
    new = asyncio.create_task(worker_b.reconcile())
    await asyncio.wait_for(entered_b.wait(), 5)
    fresh = store.get_task_state(result.task_ref)
    finish_a.set()
    assert await old is False
    assert store.get_task_state(result.task_ref) == fresh
    finish_b.set()
    assert await new == 1
    assert store.get_task_state(result.task_ref).extra["result"]["content"] == "current"


async def test_lifespan_shutdown_drains_protected_dispatch_before_cancelling(harness) -> None:
    from types import SimpleNamespace

    from hecate_durable.worker import DurableWorker

    from hecate.contracts.execution.references import task_ref
    from hecate.core.composition.wiring import stop_durable_worker

    store = harness["store"]
    ref = task_ref("hecate", str(uuid.uuid4()))
    store.apply_task_state(ref, TaskLifecycleState.QUEUED)
    started = asyncio.Event()

    async def dispatch(task, record, lease):
        started.set()
        await asyncio.sleep(0.05)
        await asyncio.to_thread(
            store.apply_task_state,
            task,
            TaskLifecycleState.SUCCEEDED,
            expected_revision=record.revision + 1,
            lease=lease,
        )

    worker = DurableWorker(store, dispatch, leases=store.leases, poll_interval=0.01)
    loop = asyncio.create_task(worker.run_forever())
    app = SimpleNamespace(
        state=SimpleNamespace(
            durable_worker=worker,
            durable_worker_task=loop,
            durable_outbox_relay=None,
            durable_relay_task=None,
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    await stop_durable_worker(app)
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.SUCCEEDED


async def test_protected_interrupt_recovers_same_run_when_session_loadable(harness, monkeypatch) -> None:
    """step6d: a protected-action interrupted attempt recovers on its OWN run
    when the engine session state is loadable; an unloadable session keeps
    the conservative reconciliation_required."""
    from hecate_durable.contracts.durable import ActionIntent, TaskLifecycleState
    from hecate_durable.contracts.references import run_ref as mk_run_ref
    from hecate_durable.contracts.tools import ToolSideEffectClass
    from sqlalchemy import select

    import hecate.execution.task_control as task_control_module
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from hecate.models.run import RunModel

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())
    store = harness["store"]
    executed_sessions: list[uuid.UUID] = []

    async def execute(self, db, context, **kwargs):
        executed_sessions.append(uuid.UUID(str(kwargs["engine_session"])))
        return {"status": "succeeded", "content": "recovered"}

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", execute)
    async with harness["session_factory"]() as db:
        result = await TaskControlService(
            db,
            store=store,
            recorder=store,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        ).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="protected recovery",
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "go"}]},
        )
    task_ref = result.task_ref

    # Simulate the interrupted attempt: revision moved past the first
    # dispatch, and the minted run carries a protected action intent from
    # the crash window.
    async with harness["session_factory"]() as db:
        run = (await db.execute(select(RunModel).where(RunModel.task_id == uuid.UUID(task_ref.id)))).scalars().first()
        engine_session = uuid.UUID(str(run.backend_ref["id"]))
    store.apply_task_state(task_ref, TaskLifecycleState.RUNNING)
    store.apply_task_state(task_ref, TaskLifecycleState.QUEUED)
    store.record_intent_ex(
        ActionIntent(
            action_key=f"{run.id}:submit_ticket",
            action_name="submit_ticket",
            arguments_digest="dig",
            side_effect_class=ToolSideEffectClass.NON_IDEMPOTENT_WRITE,
        ),
        task_ref=task_ref,
        run_ref=mk_run_ref("hecate", str(run.id)),
        session_id=str(run.id),
        execution_id=f"{run.id}:submit_ticket",
        tool_call_id=f"{run.id}:submit_ticket",
    )

    from hecate_durable.worker import DurableWorker

    dispatcher = PlatformTaskDispatcher(store, harness["session_factory"])

    async def _recoverable_true(run, context):
        return True

    # Recoverable session: the SAME run and engine session re-execute and
    # the task converges.
    monkeypatch.setattr(PlatformTaskDispatcher, "_session_recoverable", staticmethod(_recoverable_true))
    worker = DurableWorker(store, dispatcher, leases=store.leases, worker_id="rec", lease_ttl=30)
    assert await worker.dispatch_once(task_ref) is True
    assert store.get_task_state(task_ref).lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert executed_sessions == [engine_session]

    # Unloadable session on a fresh interruption: conservative reconciliation,
    # and _execute is never invoked for it.
    from hecate_durable.contracts.durable import ActionIntent
    from hecate_durable.contracts.tools import ToolSideEffectClass

    executed_sessions.clear()

    async def _recoverable_false(run, context):
        return False

    async def _fail_execute(self, db, context, **kwargs):
        raise AssertionError("must not execute when the session is not recoverable")

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", _fail_execute)
    monkeypatch.setattr(PlatformTaskDispatcher, "_session_recoverable", staticmethod(_recoverable_false))
    # Drive a second protected interruption through a fresh task.
    async with harness["session_factory"]() as db:
        result2 = await TaskControlService(
            db,
            store=store,
            recorder=store,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        ).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="protected conservative",
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "go"}]},
        )
    task2 = result2.task_ref
    async with harness["session_factory"]() as db:
        run_b = (await db.execute(select(RunModel).where(RunModel.task_id == uuid.UUID(task2.id)))).scalars().first()
    store.apply_task_state(task2, TaskLifecycleState.RUNNING)
    store.apply_task_state(task2, TaskLifecycleState.QUEUED)
    store.record_intent_ex(
        ActionIntent(
            action_key=f"{run_b.id}:submit_ticket",
            action_name="submit_ticket",
            arguments_digest="dig",
            side_effect_class=ToolSideEffectClass.NON_IDEMPOTENT_WRITE,
        ),
        task_ref=task2,
        run_ref=mk_run_ref("hecate", str(run_b.id)),
        session_id=str(run_b.id),
        execution_id=f"{run_b.id}:submit_ticket",
        tool_call_id=f"{run_b.id}:submit_ticket",
    )
    worker2 = DurableWorker(store, dispatcher, leases=store.leases, worker_id="rec2", lease_ttl=30)
    assert await worker2.dispatch_once(task2) is True
    fresh = store.get_task_state(task2)
    assert fresh.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert fresh.extra["reconciliation_reason"] == "interrupted attempt contains protected actions"
