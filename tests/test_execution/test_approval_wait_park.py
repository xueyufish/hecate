"""Durable approval wait: park, wake gate, and resume routing (step5e).

Deterministic SQLite coverage for the ``execution-approval-binding`` /
``platform-task-control`` delta specs. The engine-side signal semantics are
tested at the entry-service boundary (the signal escapes the execution, the
run finalizes with the ``waiting_approval`` stop category); the dispatcher
park mechanics, the crash-window fail-closed rule, and the wake
authorization gate run through the real task-control service with the entry
boundary stubbed at the same seam the existing task-control suite uses.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hecate_runtime.errors import ApprovalWaitSignal
from hecate_runtime.security.approval import approval_arguments_digest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.core.database import Base
from hecate.execution.task_control import TaskControlService, task_ref_of
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.approval import ApprovalAuthorityModel
from hecate.models.managed_delivery import ManagedDeliveryModel  # noqa: F401
from hecate.models.organization import OrganizationModel
from hecate.models.standalone_enrollment import StandaloneEnrollmentModel  # noqa: F401
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

WS = uuid.UUID("00000000-0000-0000-0000-000000000020")
OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000021")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000022")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000023")
AGENT = uuid.UUID("00000000-0000-0000-0000-000000000099")

PARKED_TOOL = "create_ticket"
PARKED_ARGS = {"title": "fix the ladder", "priority": "high"}


# --- engine-side signal semantics --------------------------------------------


def test_stop_category_maps_approval_wait_signal() -> None:
    """The signal maps to ``waiting_approval`` — never needs_review/failed."""

    from hecate_runtime.errors import ExecutionStoppedError

    from hecate.execution.entry_service import EntryExecutionService

    assert EntryExecutionService._stop_category(ApprovalWaitSignal(PARKED_TOOL, PARKED_ARGS, "high")) == (
        "waiting_approval"
    )
    assert EntryExecutionService._stop_category(ExecutionStoppedError("needs_review", "x")) == "needs_review"


async def test_entry_service_finalizes_waiting_approval_and_reraises(db_session, default_workspace) -> None:
    """A signal escaping the engine finalizes the run as failed with the
    waiting_approval stop category, then propagates (the dispatcher parks)."""

    import hecate.studio.workflows.execution_service as exec_module
    from hecate.execution.entry_service import CorrelationInput, EntryExecutionService

    class _SignalExec:
        def __init__(self, **kwargs) -> None:
            pass

        async def execute(self, **kwargs):
            raise ApprovalWaitSignal(PARKED_TOOL, PARKED_ARGS, "high", {"tool_call_id": "tc-1"})

    agent = AgentModel(workspace_id=default_workspace.id, name="approval-agent")
    db_session.add(agent)
    await db_session.flush()
    version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="a" * 64)
    db_session.add(version)
    await db_session.flush()
    user = UserModel(email=f"approval-{agent.id}@example.com", hashed_password=uuid.uuid4().hex)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        AgentPrincipalModel(
            agent_id=agent.id,
            workspace_id=default_workspace.id,
            organization_id=default_workspace.id,
            owner_user_id=user.id,
        )
    )
    await db_session.flush()
    db_session.add(
        AgentDeploymentModel(
            agent_id=agent.id,
            agent_version_id=version.id,
            workspace_id=default_workspace.id,
            backend_type=BackendType.BUILTIN,
            access_mode=AccessMode.IN_PROCESS,
            issuer_domain="hecate",
            is_default=True,
            capability_snapshot={},
            axes_harness="hecate",
            axes_environment="none",
            axes_tool_execution="hecate_gateway",
        )
    )
    await db_session.commit()

    real = exec_module.WorkflowExecutionService
    exec_module.WorkflowExecutionService = _SignalExec
    service = EntryExecutionService(port=object(), entry_name="task-control", db=db_session)
    try:
        with pytest.raises(ApprovalWaitSignal):
            await service.execute(
                agent_mode="chat",
                messages=[{"role": "user", "content": "create the ticket"}],
                model="stub",
                session_id=uuid.uuid4(),
                agent_id=agent.id,
                agent_version=1,
                workspace_id=default_workspace.id,
                correlation=CorrelationInput(
                    workspace_id=default_workspace.id,
                    agent_id=agent.id,
                    user_id=user.id,
                    goal="create the ticket",
                ),
            )
        from sqlalchemy import select

        from hecate.models.run import RunModel

        runs = (
            (await db_session.execute(select(RunModel).where(RunModel.workspace_id == default_workspace.id)))
            .scalars()
            .all()
        )
        assert len(runs) == 1
        projection = runs[0].projection or {}
        assert projection["state"] == "failed"
        assert projection["error_category"] == "waiting_approval"
    finally:
        exec_module.WorkflowExecutionService = real


# --- dispatcher park mechanics -------------------------------------------------


class _ApprovalParkStub:
    """Entry boundary standing in for the real execution chain.

    First dispatch: records the definition digest + a session snapshot (the
    real chain freezes both), then raises the wait signal. Authorized wake:
    returns success and records the resume fact.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._real: Any = None

    def patch(self) -> None:
        import hecate.execution.task_dispatcher as dispatcher_module

        self._real = dispatcher_module.PlatformTaskDispatcher._execute
        dispatcher_module.PlatformTaskDispatcher._execute = self._execute

    def unpatch(self) -> None:
        import hecate.execution.task_dispatcher as dispatcher_module

        dispatcher_module.PlatformTaskDispatcher._execute = self._real

    async def _execute(self, db, context, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({**kwargs, "task_id": str(context.task_id), "approval_wait": context.approval_wait})
        # Mirror the real chain: freeze the definition digest on the run and
        # persist an engine-session snapshot so the resumption gate admits
        # the authorized wake.
        from hecate.execution.task_dispatcher import _definition_digest
        from hecate.execution.task_run_registry import TaskRunRegistry

        run = await TaskRunRegistry(db).get_run(uuid.UUID(kwargs["run_ref"].id), context.workspace_id)
        if run is not None and (run.execution_snapshot or {}).get("definition_digest") is None:
            snapshot = run.execution_snapshot or {}
            config = snapshot.get("config_snapshot") or {}
            model_name = (config.get("model_config") or {}).get("model", "gpt-4o")
            run.execution_snapshot = {
                **snapshot,
                "definition_digest": _definition_digest(snapshot, [], model_name),
            }
            await db.commit()
        from hecate_runtime.session_state import SessionState

        from hecate.core.composition.entry_assembly import get_shared_session_state_store

        await get_shared_session_state_store().save(
            context.user_id, context.user_id, uuid.UUID(str(kwargs["engine_session"])), SessionState()
        )
        if kwargs.get("resume_interrupted"):
            return {"status": "succeeded", "content": "ticket created after approval"}
        # Mirror the entry service's failure finalization: the parked run
        # terminalizes with the waiting_approval stop category before the
        # signal reaches the dispatcher.
        if run is not None:
            await TaskRunRegistry(db).finalize_run(
                uuid.UUID(kwargs["run_ref"].id),
                context.workspace_id,
                state="failed",
                error_category="waiting_approval",
            )
            await db.commit()
        raise ApprovalWaitSignal(PARKED_TOOL, dict(PARKED_ARGS), "high", {"tool_call_id": "tc-1"})


@pytest.fixture
async def harness(tmp_path):
    db_file = tmp_path / "approval_wait.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})

    @event.listens_for(async_engine.sync_engine, "connect")
    def _busy_async(dbapi_conn, _record):
        # The sync durable engine and this engine share the file; both must
        # wait on SQLite's busy timeout instead of failing under contention.
        dbapi_conn.execute("PRAGMA busy_timeout=30000")

    # Switch the shared file to WAL once, deterministically, before any
    # engine connection exists: a per-connection journal_mode pragma races
    # with concurrent access (a failed switch silently stays in DELETE
    # mode, where one engine's open write transaction blocks the other's
    # reads and writes for the full busy timeout).
    import sqlite3 as _sqlite3

    with _sqlite3.connect(db_file) as raw:
        raw.execute("PRAGMA journal_mode=WAL")

    from hecate_durable.storage import SqlDurableStore

    from hecate.core.composition.durable_platform import to_sync_database_url

    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.core.composition.durable_platform import DurableSuite

    suite = DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core")

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(WorkspaceModel(id=OTHER_WS, org_id=ORG, name="other", slug=f"ws-{OTHER_WS.hex}"))
        db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="approval-task-agent")
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

    state = {
        "session_factory": session_factory,
        "suite": suite,
        "agent_id": agent_id,
        "engine": async_engine,
        "store": store,
        "sync_url": to_sync_database_url(f"sqlite+aiosqlite:///{db_file}"),
        "cleanups": [],
    }
    yield state

    for cleanup in reversed(state["cleanups"]):
        cleanup()
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


async def _submit(harness, db: AsyncSession, service: TaskControlService, **overrides: Any):
    kwargs: dict[str, Any] = dict(
        workspace_id=WS,
        user_id=OWNER,
        goal="create the ticket",
        agent_id=harness["agent_id"],
        input={"messages": [{"role": "user", "content": "create the ticket"}], "approval_wait": True},
    )
    kwargs.update(overrides)
    return await service.submit(**kwargs)


async def _wait_for_state(harness, t_ref, *states: str, timeout: float = 20.0, worker: Any | None = None) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
        if record is not None and record.lifecycle_state.value in states:
            return record.lifecycle_state.value
        if worker is not None and record is not None and record.lifecycle_state is TaskLifecycleState.QUEUED:
            await worker.dispatch_once(t_ref)
        await asyncio.sleep(0.05)
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    raise AssertionError(f"task never reached {states}; last={record.lifecycle_state.value if record else None}")


async def _task_detail(harness, task_uuid: uuid.UUID) -> dict[str, Any]:
    async with harness["session_factory"]() as db:
        return await _service(harness, db).get_task_detail(WS, task_uuid)


async def _task_result(harness, t_ref) -> dict[str, Any]:
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    return (record.extra or {}).get("result") or {}


async def _binding_of(harness, approval_ref: str):
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        return await SqlApprovalLedger(db).get(approval_ref)


async def _parked_task(harness):
    """Submit through the worker path and wait for the approval park."""

    from hecate_durable.worker import DurableWorker

    stub = _ApprovalParkStub()
    stub.patch()
    harness["cleanups"].append(stub.unpatch)
    worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, _service(harness, db, worker=worker))
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = task_ref_of(task_uuid)
    await _wait_for_state(harness, t_ref, "waiting_approval", "failed")
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL, state.extra
    detail = await _task_detail(harness, task_uuid)
    return task_uuid, t_ref, detail, worker, stub


async def _issue_resume(harness, task_uuid: uuid.UUID, token: str, worker: Any, payload: dict | None = None):
    body = payload or {}
    async with harness["session_factory"]() as db:
        issued = await _service(harness, db, worker=worker).issue_command(
            workspace_id=WS,
            task_id=task_uuid,
            kind=ControlCommandKind.RESUME,
            issuer=str(OWNER),
            detail_ns={"wait_token": token},
            payload=body,
            payload_schema_ref="test:approval-resume/0" if body else None,
        )
    # A worker deployment requeues only; drive the re-dispatch like the
    # worker's own cycle would.
    if worker is not None and issued.record.state is CommandState.APPLIED:
        await worker.dispatch_once(task_ref_of(task_uuid))
    return issued


async def _decide(harness, ref: str, *, approved: bool, reason: str) -> None:
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        await SqlApprovalLedger(db).decide(ref, approved=approved, reason=reason, decided_by=str(OWNER))
        await db.commit()


async def test_approval_signal_parks_task_with_pending_binding(harness) -> None:
    """Park = waiting_approval state + wait contract with approval_ref +
    a PENDING authority binding scoped to the task with the pinned digest."""

    task_uuid, _t_ref, detail, _worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    contract = wait["contract_ref"]
    assert wait["wake_kind"] == "resume"
    assert wait["consumed"] is False
    assert contract["tool_name"] == PARKED_TOOL
    assert contract["approval_ref"]

    binding = await _binding_of(harness, contract["approval_ref"])
    assert binding is not None
    from hecate_governance.approval import ApprovalJudgment

    assert binding.judgment is ApprovalJudgment.PENDING
    assert binding.tool_name == PARKED_TOOL
    assert binding.arguments_digest == approval_arguments_digest(PARKED_ARGS)
    assert binding.task_scope == str(task_uuid)
    assert binding.consumed_by_invocation_id is None

    # The parked run finalizes with the waiting_approval stop category.
    runs = detail["runs"]
    assert runs and runs[-1]["projection"]["state"] == "failed"
    assert runs[-1]["projection"]["error_category"] == "waiting_approval"


async def test_park_persistence_failure_fails_task(harness, monkeypatch) -> None:
    """A binding-mint failure fails the task — never run unapproved."""

    import hecate.execution.approval_wait as approval_wait_module

    def _boom(*args, **kwargs):
        raise RuntimeError("authority store unavailable")

    monkeypatch.setattr(approval_wait_module, "mint_pending_approval_binding", _boom)
    stub = _ApprovalParkStub()
    stub.patch()
    harness["cleanups"].append(stub.unpatch)
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, _service(harness, db, worker=worker))
    t_ref = task_ref_of(uuid.UUID(result.task_ref.id))
    assert await _wait_for_state(harness, t_ref, "failed") == "failed"
    assert (await _task_result(harness, t_ref))["error"] == "approval wait could not be persisted; fail closed"


async def test_crash_window_waiting_approval_without_contract_fails_closed(harness) -> None:
    """Run terminal (waiting_approval) + no consumed wait contract → the
    dispatch backfills a FAILED terminal instead of continuing."""

    # Park normally first (stub patches the entry boundary), then strip the
    # wait contract's consumed flag to simulate the crash between the run
    # finalization and the park write.
    task_uuid, t_ref, _detail, worker, _stub = await _parked_task(harness)
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        t_ref,
        TaskLifecycleState.QUEUED,
        extra_update={"wait": {"wake_kind": "resume", "contract_ref": {}, "wait_token": "x", "consumed": False}},
    )
    async with harness["session_factory"]() as db:
        await _service(harness, db, worker=worker)._dispatch_now(t_ref)
    assert await _wait_for_state(harness, t_ref, "failed", worker=worker) == "failed"
    assert "wait contract" in str((await _task_result(harness, t_ref)).get("error"))


async def test_authorized_wake_resumes_same_attempt_and_succeeds(harness) -> None:
    """decide(approved) + resume → the resumption gate admits the parked
    attempt (same run/engine session) and the stub completes it."""

    task_uuid, t_ref, detail, worker, stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]
    first_run = detail["runs"][-1]["run_id"]

    await _decide(harness, ref, approved=True, reason="ladder fix approved")
    issued = await _issue_resume(harness, task_uuid, wait["wait_token"], worker, payload={"approval_ref": ref})
    assert issued.record.state is CommandState.APPLIED
    assert await _wait_for_state(harness, t_ref, "succeeded") == "succeeded"

    final = await _task_detail(harness, task_uuid)
    assert final["lifecycle_state"] == "succeeded"
    assert (await _task_result(harness, t_ref))["content"] == "ticket created after approval"
    # The authorized wake CONTINUES the parked attempt (same run) — the
    # session-keyed action-ledger recovery stays valid (design D4).
    assert final["runs"][-1]["run_id"] == first_run
    assert stub.calls[-1]["resume_interrupted"] is True


async def test_controller_approval_wake_retains_authority_control_payload(harness, monkeypatch) -> None:
    """Controller input history must not drop the approval consumer's reference."""
    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    stub = _ApprovalParkStub()
    monkeypatch.setattr(PlatformTaskDispatcher, "_execute_controller", stub._execute)
    worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
    async with harness["session_factory"]() as db:
        result = await _submit(
            harness,
            db,
            _service(harness, db, worker=worker),
            input={
                "messages": [{"role": "user", "content": "create the ticket"}],
                "approval_wait": True,
                "controller_loop": True,
            },
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    task_ref = task_ref_of(task_uuid)
    await _wait_for_state(harness, task_ref, "waiting_approval")
    detail = await _task_detail(harness, task_uuid)
    wait = detail["wait"]
    reference = wait["contract_ref"]["approval_ref"]
    await _decide(harness, reference, approved=True, reason="approved")
    receipt = await _issue_resume(harness, task_uuid, wait["wait_token"], worker)
    assert receipt.record.state is CommandState.APPLIED
    payload = await asyncio.to_thread(harness["store"].get_task_input, task_ref)
    assert payload["provided"]["approval_ref"] == reference
    assert await asyncio.to_thread(harness["store"].list_input_revisions, task_ref) == []


async def test_pending_decision_refuses_wake_without_consuming_token(harness) -> None:
    task_uuid, t_ref, detail, worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]

    issued = await _issue_resume(
        harness, task_uuid, wait["wait_token"], worker, payload={"approval_ref": ref, "approved": True}
    )
    assert issued.record.state is CommandState.REJECTED
    assert "pending" in issued.detail
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    fresh = (await asyncio.to_thread(harness["store"].get_task_state, t_ref)).extra.get("wait") or {}
    assert fresh.get("consumed") is False

    # The still-valid token wakes after a real decision arrives.
    await _decide(harness, ref, approved=True, reason="ok")
    second = await _issue_resume(harness, task_uuid, wait["wait_token"], worker)
    assert second.record.state is CommandState.APPLIED
    assert await _wait_for_state(harness, t_ref, "succeeded") == "succeeded"


async def test_rejected_decision_consumes_token_and_fails_task(harness) -> None:
    task_uuid, t_ref, detail, worker, stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]

    await _decide(harness, ref, approved=False, reason="not now")
    issued = await _issue_resume(harness, task_uuid, wait["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED
    assert await _wait_for_state(harness, t_ref, "failed") == "failed"
    assert (await _task_result(harness, t_ref))["error"] == "approval_rejected"
    fresh = (await asyncio.to_thread(harness["store"].get_task_state, t_ref)).extra.get("wait") or {}
    assert fresh.get("consumed") is True
    # The protected action never executed: the resume path never ran.
    assert not any(call.get("resume_interrupted") for call in stub.calls)


async def test_wake_gate_revoked_and_payload_mismatch_refuse(harness) -> None:
    """A revoked approval and a mismatched payload ref both refuse the wake
    and leave the token unconsumed."""

    task_uuid, t_ref, detail, worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]
    token = wait["wait_token"]

    mismatch = await _issue_resume(harness, task_uuid, token, worker, payload={"approval_ref": "task:other:ref"})
    assert mismatch.record.state is CommandState.REJECTED
    assert "match" in mismatch.detail

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        ledger = SqlApprovalLedger(db)
        await ledger.decide(ref, approved=True, reason="ok", decided_by=str(OWNER))
        await ledger.revoke(ref)
        await db.commit()
    revoked = await _issue_resume(harness, task_uuid, token, worker)
    assert revoked.record.state is CommandState.REJECTED
    assert "revoked" in revoked.detail
    fresh = (await asyncio.to_thread(harness["store"].get_task_state, t_ref)).extra.get("wait") or {}
    assert fresh.get("consumed") is False


async def test_expired_approved_binding_refuses_wake(harness) -> None:
    """An approved-but-expired binding fails the wake's binding check; the
    token stays unconsumed and the wait stays resumable-shaped (refused)."""

    task_uuid, t_ref, detail, worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]

    await _decide(harness, ref, approved=True, reason="too late")
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(ApprovalAuthorityModel.approval_ref == ref)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await db.commit()

    issued = await _issue_resume(harness, task_uuid, wait["wait_token"], worker)
    assert issued.record.state is CommandState.REJECTED
    assert "expired" in issued.detail
    fresh = (await asyncio.to_thread(harness["store"].get_task_state, t_ref)).extra.get("wait") or {}
    assert fresh.get("consumed") is False


# --- resume consumer unit semantics --------------------------------------------


async def test_durable_binding_consumer_consumes_matching_call() -> None:
    """A matching call consumes once; a different protected call re-signals."""

    from hecate_governance.approval import MemoryApprovalLedger

    from hecate.execution.approval_wait import DurableBindingConsumer, mint_pending_approval_binding

    ledger = MemoryApprovalLedger()
    task_id = uuid.uuid4()
    actor = str(OWNER)
    ref, binding = mint_pending_approval_binding(
        ledger,
        task_id=task_id,
        actor=actor,
        agent_principal=str(AGENT),
        tool_name=PARKED_TOOL,
        arguments=dict(PARKED_ARGS),
    )
    await ledger.save(binding)
    await ledger.decide(ref, approved=True, reason="ok", decided_by="tester")

    consumer = DurableBindingConsumer(
        ledger,
        approval_ref=ref,
        task_id=task_id,
        actor=actor,
        agent_principal=str(AGENT),
        binding_tool_name=PARKED_TOOL,
        binding_arguments_digest=binding.arguments_digest,
    )
    decision = await consumer.request_approval(PARKED_TOOL, dict(PARKED_ARGS), "high", {"invocation_id": "inv-1"})
    assert decision.approved is True
    assert decision.approval_ref == ref

    # Re-issue (double consume by a different action) is refused.
    from hecate_runtime.errors import ExecutionStoppedError

    with pytest.raises(ExecutionStoppedError, match="approval_conflict"):
        await consumer.request_approval(PARKED_TOOL, dict(PARKED_ARGS), "high", {"invocation_id": "inv-2"})

    # A different protected call re-signals (new park), not denial-silent.
    with pytest.raises(ApprovalWaitSignal):
        await consumer.request_approval("delete_ticket", {"id": 1}, "high", {"invocation_id": "inv-3"})

    # Parameter drift on the parked tool re-signals too.
    with pytest.raises(ApprovalWaitSignal):
        await consumer.request_approval(PARKED_TOOL, {**PARKED_ARGS, "priority": "low"}, "high", {})


async def test_durable_wait_backend_raises_signal() -> None:
    from hecate.execution.approval_wait import DurableWaitApprovalBackend

    with pytest.raises(ApprovalWaitSignal) as exc_info:
        await DurableWaitApprovalBackend().request_approval(PARKED_TOOL, dict(PARKED_ARGS), "high", {})
    assert exc_info.value.tool_name == PARKED_TOOL
    assert exc_info.value.arguments == PARKED_ARGS


async def test_wired_resume_preserves_original_authority(harness) -> None:
    """Resume must return the decided record, without minting another grant."""
    from types import SimpleNamespace

    from hecate_governance.approval import ApprovalJudgment

    from hecate.enterprise.approval_binding import AuthorityBackedApprovalCallback
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from hecate.execution.approval_wait import mint_pending_approval_binding
    from hecate.execution.task_dispatcher import _DispatchContext

    task_id = uuid.uuid4()
    async with harness["session_factory"]() as db:
        ledger = SqlApprovalLedger(db)
        ref, binding = mint_pending_approval_binding(
            ledger,
            task_id=task_id,
            actor=str(OWNER),
            agent_principal=str(AGENT),
            tool_name=PARKED_TOOL,
            arguments=PARKED_ARGS,
        )
        await ledger.save(binding)
        await ledger.decide(ref, approved=True, reason="approved", decided_by=str(OWNER))
        await db.commit()
        bundle = SimpleNamespace(
            approval_callback=AuthorityBackedApprovalCallback(
                authority=ledger,
                session_id=uuid.uuid4(),
                workspace_id=WS,
                agent_id=AGENT,
                identity={"actor": str(OWNER), "agent_principal": str(AGENT)},
            )
        )
        context = _DispatchContext(
            task_id=task_id,
            workspace_id=WS,
            agent_id=AGENT,
            user_id=OWNER,
            goal="test",
            messages=[],
            model=None,
            stream=False,
            provided={"approval_ref": ref},
            approval_wait=True,
        )
        await _dispatcher(harness)._wire_approval_backend(db, context, bundle, resume_interrupted=True)
        decision = await bundle.approval_callback.request_approval(
            PARKED_TOOL,
            PARKED_ARGS,
            "high",
            {"invocation_id": "stable-action", "tool_call_id": "call-1"},
        )
        assert decision.approved
        assert decision.approval_ref == ref
        saved = await ledger.get(ref)
        assert saved.judgment is ApprovalJudgment.APPROVED
        assert saved.consumed_by_invocation_id in (None, "stable-action")


async def test_wake_rejects_binding_from_another_task(harness) -> None:
    """Matching tool arguments cannot substitute a different task's authority."""
    task_id, t_ref, detail, worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="approved")
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(
                ApprovalAuthorityModel.approval_ref == ref,
            )
            .values(task_scope=str(uuid.uuid4()))
        )
        await db.commit()
    issued = await _issue_resume(harness, task_id, wait["wait_token"], worker)
    assert issued.record.state is CommandState.REJECTED
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    assert state.extra["wait"]["consumed"] is False


async def test_rejected_approval_updates_run_and_terminal_event(harness) -> None:
    """Rejection must leave queryable terminal Run facts as well as Task state."""
    task_id, _t_ref, detail, worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    await _decide(harness, wait["contract_ref"]["approval_ref"], approved=False, reason="rejected")
    issued = await _issue_resume(harness, task_id, wait["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED
    final = await _task_detail(harness, task_id)
    assert final["runs"][-1]["projection"].get("error_category") == "approval_rejected"
    from hecate.execution.governance_events import RUN_TERMINAL

    async with harness["session_factory"]() as db:
        page = await _service(harness, db, worker=worker).read_run_events(
            WS,
            uuid.UUID(final["runs"][-1]["run_id"]),
        )
    terminal = [item for item in page.events if item.payload_schema_ref == RUN_TERMINAL]
    assert len(terminal) == 1
    assert terminal[0].payload["error_category"] == "approval_rejected"
    after_relay = await _task_detail(harness, task_id)
    assert after_relay["runs"][-1]["projection"]["error_category"] == "approval_rejected"


async def test_wait_contract_write_failure_is_terminal(harness, monkeypatch) -> None:
    """A failed park write cannot leave the worker retrying an unrecorded wait."""
    from hecate_durable.worker import DurableWorker

    store = harness["store"]
    real_apply = store.apply_task_state

    def apply(task_ref, state, **kwargs):
        if state is TaskLifecycleState.WAITING_APPROVAL:
            raise RuntimeError("wait storage unavailable")
        return real_apply(task_ref, state, **kwargs)

    monkeypatch.setattr(store, "apply_task_state", apply)
    stub = _ApprovalParkStub()
    stub.patch()
    harness["cleanups"].append(stub.unpatch)
    worker = DurableWorker(store, _dispatcher(harness), leases=store.leases)
    async with harness["session_factory"]() as db:
        submitted = await _submit(harness, db, _service(harness, db, worker=worker))
    task_ref = task_ref_of(uuid.UUID(submitted.task_ref.id))
    await _wait_for_state(harness, task_ref, "failed", timeout=2)


async def test_consumer_rebinds_same_action_and_stops_after_revocation() -> None:
    """A consumed grant survives a pre-dispatch crash, but never revocation."""
    from hecate_governance.approval import MemoryApprovalLedger
    from hecate_runtime.errors import ExecutionStoppedError
    from hecate_runtime.security.approval import FailingClosedApprovalCallback

    from hecate.execution.approval_wait import DurableBindingConsumer, mint_pending_approval_binding

    ledger = MemoryApprovalLedger()
    task_id = uuid.uuid4()
    ref, binding = mint_pending_approval_binding(
        ledger,
        task_id=task_id,
        actor=str(OWNER),
        agent_principal=str(AGENT),
        tool_name=PARKED_TOOL,
        arguments=PARKED_ARGS,
    )
    await ledger.save(binding)
    await ledger.decide(ref, approved=True, reason="ok", decided_by=str(OWNER))
    call = {"invocation_id": "stable-before-claim", "tool_call_id": "call-1"}
    for _ in range(2):
        callback = FailingClosedApprovalCallback()
        callback._inner_backend = DurableBindingConsumer(
            ledger,
            approval_ref=ref,
            task_id=task_id,
            actor=str(OWNER),
            agent_principal=str(AGENT),
        )
        decision = await callback.request_approval(PARKED_TOOL, PARKED_ARGS, "high", call)
        assert decision.approved and decision.approval_ref == ref
    await ledger.revoke(ref)
    callback = FailingClosedApprovalCallback()
    callback._inner_backend = DurableBindingConsumer(
        ledger,
        approval_ref=ref,
        task_id=task_id,
        actor=str(OWNER),
        agent_principal=str(AGENT),
    )
    with pytest.raises(ExecutionStoppedError, match="approval_revoked"):
        await callback.request_approval(PARKED_TOOL, PARKED_ARGS, "high", call)


async def test_task_authority_reanchors_restart_without_new_grant(harness) -> None:
    """Online database time can re-anchor a foreign epoch without extending expiry."""
    from hecate_governance.clock import PROCESS_EPOCH

    from hecate.enterprise.approval_wait_ledger import task_approval_ledger

    task_id, _t_ref, detail, _worker, _stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="ok")
    original = await _binding_of(harness, ref)
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(
                ApprovalAuthorityModel.approval_ref == ref,
            )
            .values(clock_epoch="previous-worker")
        )
        await db.commit()
    async with harness["session_factory"]() as db:
        binding = await task_approval_ledger(db).get(ref)
        assert binding.clock_epoch == PROCESS_EPOCH
        assert binding.task_scope == str(task_id)
        assert binding.approval_ref == ref
        assert binding.expires_at == original.expires_at
        assert binding.decided_by == original.decided_by


async def test_wake_authority_store_failure_leaves_resumable_token(harness, monkeypatch) -> None:
    """Authority outages produce a rejected command, never a lost wait token."""
    task_id, t_ref, detail, worker, _stub = await _parked_task(harness)

    class UnavailableLedger:
        async def get(self, ref):
            raise ConnectionError("authority unavailable")

    monkeypatch.setattr(TaskControlService, "_approval_ledger", lambda self: UnavailableLedger())
    issued = await _issue_resume(harness, task_id, detail["wait"]["wait_token"], worker)
    assert issued.record.state is CommandState.REJECTED
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    assert state.extra["wait"]["consumed"] is False


async def test_approval_wait_rejects_foreign_workspace_and_duplicate_resume(harness) -> None:
    """A wake is scoped to its workspace and the token is applied only once."""
    from hecate.execution.task_control import TaskControlNotFoundError

    task_id, _t_ref, detail, worker, stub = await _parked_task(harness)
    wait = detail["wait"]
    await _decide(harness, wait["contract_ref"]["approval_ref"], approved=True, reason="ok")
    async with harness["session_factory"]() as db:
        with pytest.raises(TaskControlNotFoundError):
            await _service(harness, db, worker=worker).issue_command(
                workspace_id=OTHER_WS,
                task_id=task_id,
                kind=ControlCommandKind.RESUME,
                issuer=str(OWNER),
                detail_ns={"wait_token": wait["wait_token"]},
            )
    first = await _issue_resume(harness, task_id, wait["wait_token"], worker)
    second = await _issue_resume(harness, task_id, wait["wait_token"], worker)
    assert first.record.state is CommandState.APPLIED
    assert second.record.state is CommandState.REJECTED
    assert len(stub.calls) == 2


@pytest.mark.parametrize("field,value", [("arguments_digest", "drifted"), ("principal_fingerprint", "other-principal")])
async def test_wake_refuses_binding_context_drift(harness, field, value) -> None:
    """The wake gate compares authority with persisted task and action facts."""
    task_id, t_ref, detail, worker, _stub = await _parked_task(harness)
    wait = detail["wait"]
    ref = wait["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="ok")
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(
                ApprovalAuthorityModel.approval_ref == ref,
            )
            .values(**{field: value})
        )
        await db.commit()
    issued = await _issue_resume(harness, task_id, wait["wait_token"], worker)
    assert issued.record.state is CommandState.REJECTED
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    assert state.extra["wait"]["consumed"] is False


async def test_terminal_run_is_backfilled_despite_old_consumed_wait(harness) -> None:
    """A completed Run must not be reopened by a previous approval wake."""
    from hecate.execution.task_run_registry import TaskRunRegistry

    task_id, t_ref, detail, worker, stub = await _parked_task(harness)
    wait = detail["wait"]
    async with harness["session_factory"]() as db:
        await TaskRunRegistry(db).finalize_run(
            uuid.UUID(detail["runs"][-1]["run_id"]),
            WS,
            state="succeeded",
            summary="already finished",
        )
        await db.commit()
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        t_ref,
        TaskLifecycleState.QUEUED,
        extra_update={"wait": {**wait, "consumed": True}},
    )
    await worker.dispatch_once(t_ref)
    assert await _wait_for_state(harness, t_ref, "succeeded") == "succeeded"
    assert len(stub.calls) == 1


async def test_authority_rejects_clock_rollback_on_restart(harness) -> None:
    """Re-anchoring cannot erase a persisted future authority-time anchor."""
    from hecate.enterprise.approval_wait_ledger import task_approval_ledger

    _task_id, _t_ref, detail, _worker, _stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(
                ApprovalAuthorityModel.approval_ref == ref,
            )
            .values(clock_epoch="old-process", anchor_wall=datetime.now(UTC) + timedelta(hours=1))
        )
        await db.commit()
    async with harness["session_factory"]() as db:
        with pytest.raises(ValueError, match="moved backwards"):
            await task_approval_ledger(db).get(ref)


async def test_approval_action_identity_matches_tool_dispatch() -> None:
    """Approval consumption and the actual tool dispatch share one server ID."""
    from types import SimpleNamespace

    from hecate_runtime.tool_access import AccessDecision, ApprovalDecision
    from hecate_runtime.workers.tool_worker import ToolWorker

    seen = []

    class Callback:
        async def request_approval(self, **kwargs):
            seen.append(kwargs["context"]["invocation_id"])
            return ApprovalDecision(approved=True, approval_ref="original-grant")

    class Port:
        async def tool_execute(self, name, args, context=None):
            seen.append(context["invocation_id"])
            return "receipt"

        async def create_span(self, *args, **kwargs):
            return None

    policy = SimpleNamespace(evaluate=lambda *args, **kwargs: AccessDecision.REQUIRE_APPROVAL)
    worker = ToolWorker(port=Port(), access_policy=policy, approval_callback=Callback())
    await worker.execute(
        "tool",
        {},
        {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call-fixed", "function": {"name": "synthetic_action", "arguments": {}}},
                    ],
                }
            ]
        },
        {"session_id": uuid.uuid4()},
    )
    assert len(seen) == 2 and seen[0] == seen[1]
