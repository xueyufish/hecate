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
