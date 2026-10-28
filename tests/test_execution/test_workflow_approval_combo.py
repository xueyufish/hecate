"""Workflow × durable-approval combination (step5e continuation).

Deterministic SQLite coverage for the parent-child composition: the step
declaration carries `approval_wait` into the child's persisted input, the
parent holds its durable wait while the child parks, an approved child
resumes natively and the parent advances only after artifact validation,
a rejected decision converges the parent through the verified callback
(both on_failure policies), and forged callbacks while the child is still
waiting are refused by the existing verification semantics.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from hecate_runtime.errors import ApprovalWaitSignal
from hecate_runtime.execution_assembly import system_execution_tenant
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.core.database import Base
from hecate.execution.task_control import TaskControlService, task_ref_of
from hecate.execution.workflow_child import WorkflowChildTaskAdapter
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.managed_delivery import ManagedDeliveryModel  # noqa: F401
from hecate.models.organization import OrganizationModel
from hecate.models.standalone_enrollment import StandaloneEnrollmentModel  # noqa: F401
from hecate.models.tool_policy import ToolPolicyModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

WS = uuid.UUID("00000000-0000-0000-0000-000000000030")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000031")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000032")

PARKED_TOOL = "create_ticket"
PARKED_ARGS = {"title": "fix the ladder"}
STEP_CONTRACT = {
    "goal": "create the ladder ticket",
    "approval_wait": True,
    "output_schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    },
    "artifact": "answer",
}


# --- declaration parsing (task 1.1) --------------------------------------------


def test_step_declaration_approval_wait_stamp() -> None:
    """The declaration is validated and stamped into the child input."""

    parent_ref = task_ref_of(uuid.uuid4())
    stamped = WorkflowChildTaskAdapter.stamp_step_input("g", parent_ref, 0, dict(STEP_CONTRACT))
    assert stamped["approval_wait"] is True
    assert stamped["workflow_step"]["approval_wait"] is True

    plain = WorkflowChildTaskAdapter.stamp_step_input("g", parent_ref, 1, {"goal": "plain step"})
    assert plain["approval_wait"] is False

    default = WorkflowChildTaskAdapter.stamp_step_input("g", parent_ref, 2)
    assert default["approval_wait"] is False

    plan = WorkflowChildTaskAdapter.plan({"workflow": {"steps": [dict(STEP_CONTRACT), {"goal": "plain"}]}})
    assert plan is not None and plan.steps[0]["approval_wait"] is True


@pytest.mark.parametrize("invalid", [None, "yes", 1, 0, {}, []])
def test_step_declaration_approval_wait_type_rejected(invalid) -> None:
    """A non-boolean declaration is rejected with a locatable error."""

    with pytest.raises(ValueError, match="approval_wait must be a boolean"):
        WorkflowChildTaskAdapter.plan({"workflow": {"steps": [{"goal": "g", "approval_wait": invalid}]}})


# --- harness --------------------------------------------------------------------


class _WorkflowParkStub:
    """Entry boundary: approval-declaring children park, others succeed.

    Mirrors the real chain for the parked attempt: freezes the definition
    digest, persists an engine-session snapshot under the SYSTEM tenant key
    (the child has no human user — the derived key is the production save
    address), finalizes the run with the waiting_approval category, then
    raises the signal. Authorized wakes return the declared artifact.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.side_effects: list[dict[str, Any]] = []
        self._real: Any = None
        self.resume_content = '{"answer": "ticket TCK-1 created"}'

    def patch(self) -> None:
        import hecate.execution.task_dispatcher as dispatcher_module

        self._real = dispatcher_module.PlatformTaskDispatcher._execute
        dispatcher_module.PlatformTaskDispatcher._execute = self._execute

    def unpatch(self) -> None:
        import hecate.execution.task_dispatcher as dispatcher_module

        dispatcher_module.PlatformTaskDispatcher._execute = self._real

    async def _execute(self, db, context, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"task": str(context.task_id), "approval_wait": context.approval_wait})
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

        tenant = context.user_id or system_execution_tenant(context.agent_id)
        await get_shared_session_state_store().save(
            tenant, tenant, uuid.UUID(str(kwargs["engine_session"])), SessionState()
        )
        if kwargs.get("resume_interrupted"):
            return {
                "status": "succeeded",
                "content": self.resume_content,
                "artifacts": {"answer": "ticket TCK-1 created"},
            }
        if not context.approval_wait:
            self.side_effects.append({"task": str(context.task_id)})
            return {
                "status": "succeeded",
                "content": '{"answer": "plain step done"}',
                "artifacts": {"answer": "plain step done"},
            }
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
async def harness(tmp_path, monkeypatch):
    # Drive claims explicitly; background dispatch would outlive the fixture
    # and race SQLite's synchronous outbox receipts on the event-loop thread.
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coroutine: coroutine.close())
    from hecate_runtime.session_state import InMemorySessionStateStore

    monkeypatch.setattr(
        "hecate.core.composition.entry_assembly._shared_session_state_store", InMemorySessionStateStore()
    )
    db_file = tmp_path / "wf_approval.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    @event.listens_for(async_engine.sync_engine, "connect")
    def _wal_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        # The pump + background dispatch cascade write concurrently; SQLite's
        # default busy handler is zero, so a single writer collision aborts.
        dbapi_conn.execute("PRAGMA busy_timeout=30000")

    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()

    @event.listens_for(store.engine, "connect")
    def _wal_sync(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=30000")

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.core.composition.durable_platform import DurableSuite

    suite = DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core")

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="wf-approval-agent")
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
        db.add(
            ToolPolicyModel(
                workspace_id=WS,
                rule_action="ask",
                tool_pattern=PARKED_TOOL,
                priority=10,
                description="protected action",
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


async def _wait_for_state(harness, t_ref, *states: str, timeout: float = 60.0, worker: Any | None = None) -> Any:
    """Poll persisted state while explicitly driving claim-safe dispatches."""

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if worker is not None:
            await worker.reconcile()
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
        if record is not None and record.lifecycle_state.value in states:
            return record
        await asyncio.sleep(0.25)
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    import traceback

    for task in asyncio.all_tasks():
        if task.done():
            continue
        frames = task.get_stack()
        if frames:
            stack = "".join(traceback.format_stack(frames[-1]))[-1200:]
            print(f"[hang] {task.get_name()}: {stack}", flush=True)
    raise AssertionError(
        "task never reached "
        f"{states}; last={record.lifecycle_state.value if record else None} "
        f"extra={getattr(record, 'extra', None)}"
    )


async def _task_detail(harness, task_uuid: uuid.UUID) -> dict[str, Any]:
    async with harness["session_factory"]() as db:
        return await _service(harness, db).get_task_detail(WS, task_uuid)


async def _task_result(harness, t_ref) -> dict[str, Any]:
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    return (record.extra or {}).get("result") or {}


async def _decide(harness, ref: str, *, approved: bool, reason: str) -> None:
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        await SqlApprovalLedger(db).decide(ref, approved=approved, reason=reason, decided_by=str(OWNER))
        await db.commit()


async def _submit_workflow(harness, *, on_failure: str = "fail") -> Any:
    from hecate_durable.worker import DurableWorker

    stub = _WorkflowParkStub()
    stub.patch()
    harness["cleanups"].append(stub.unpatch)
    steps = [dict(STEP_CONTRACT), {"goal": "plain closing step"}]
    worker_obj = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
    async with harness["session_factory"]() as db:
        result = await _service(harness, db, worker=worker_obj).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="run the two-step workflow",
            agent_id=harness["agent_id"],
            input={
                "messages": [{"role": "user", "content": "run"}],
                "workflow": {"steps": steps, "on_failure": on_failure},
            },
        )
    parent_uuid = uuid.UUID(result.task_ref.id)
    parent_ref = task_ref_of(parent_uuid)
    record = await _wait_for_state(harness, parent_ref, "waiting_input", "failed", worker=worker_obj)
    return parent_uuid, parent_ref, record, worker_obj, stub


async def _find_child(
    harness, parent_uuid: uuid.UUID, step_index: int, worker: Any | None = None
) -> tuple[uuid.UUID, Any]:
    """The parked child of one step, found through the parent's run facts."""

    # The child task id rides the parent's persisted input scan: find the
    # queued/waiting task whose workflow_parent stamp names this parent.
    async with harness["session_factory"]() as db:
        from sqlalchemy import select

        from hecate.models.task import TaskModel

        candidates = (await db.execute(select(TaskModel).where(TaskModel.workspace_id == WS))).scalars().all()
    for task in candidates:
        if str(task.id) == str(parent_uuid):
            continue
        child_ref = task_ref_of(task.id)
        payload = await asyncio.to_thread(harness["store"].get_task_input, child_ref) or {}
        stamp = payload.get("workflow_parent") or {}
        if stamp.get("task_ref", {}).get("id") == str(parent_uuid) and stamp.get("step_index") == step_index:
            # The child's inline dispatch may lag the parent's park.
            await _wait_for_state(harness, child_ref, "waiting_approval", "failed", "succeeded", worker=worker)
            return task.id, child_ref
    raise AssertionError(f"no child task found for parent {parent_uuid} step {step_index}")


# --- combination scenarios --------------------------------------------------------


async def _issue_resume(harness, task_uuid: uuid.UUID, token: str, worker: Any, payload: dict | None = None):
    """Issue the approval resume and drive the worker's single dispatch."""

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
    if worker is not None and issued.record.state is CommandState.APPLIED:
        await worker.dispatch_once(task_ref_of(task_uuid))
    return issued


async def test_child_park_holds_parent_and_resume_advances(harness) -> None:
    """Child parks → parent holds → approve → child resumes with the
    artifact → parent validates and advances to the plain step → succeeds."""

    parent_uuid, parent_ref, _parked, worker, stub = await _submit_workflow(harness)
    parent_detail = await _task_detail(harness, parent_uuid)
    assert parent_detail["lifecycle_state"] == "waiting_input"
    parent_wait = (await asyncio.to_thread(harness["store"].get_task_state, parent_ref)).extra.get("wait") or {}
    assert parent_wait.get("consumed") is False

    child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)
    child_record = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    child_wait = child_record.extra["wait"]
    assert child_wait["contract_ref"]["approval_ref"]
    # The parent's wait is bound to THIS child (await_task_ref contract).
    bound_child = parent_detail["wait"]["contract_ref"]["await_task_ref"]["id"]
    assert bound_child == str(child_id)

    ref = child_wait["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="ladder approved")
    issued = await _issue_resume(harness, child_id, child_wait["wait_token"], worker, payload={"approval_ref": ref})
    assert issued.record.state is CommandState.APPLIED
    await _wait_for_state(harness, child_ref, "succeeded", "failed", timeout=120.0, worker=worker)
    # The parent converges through the verified callback chain: child 0
    # completes, the parent validates the artifact and advances, child 1
    # (plain) runs, and the workflow reaches terminal success. Each hop is
    # a background spawn chain; the budget covers the full cascade.
    final = await _wait_for_state(
        harness, parent_ref, "succeeded", "failed", "reconciliation_required", timeout=150.0, worker=worker
    )
    if final.lifecycle_state.value != "succeeded":
        child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
        raise AssertionError(
            f"parent ended {final.lifecycle_state.value}; "
            f"parent_extra={getattr(final, 'extra', None)}; "
            f"child_state={child_state.lifecycle_state.value if child_state else None} "
            f"child_extra={getattr(child_state, 'extra', None)}"
        )
    parent_result = await _task_result(harness, parent_ref)
    assert parent_result["status"] == "succeeded"
    # Dispatch calls: the approval child is driven at least twice (park +
    # native resume on the same run); a lease-expiry recovery may replay the
    # resume idempotently (receipt replay, no second effect). Two DISTINCT
    # child tasks total.
    assert len(stub.calls) >= 3, stub.calls
    assert len({call["task"] for call in stub.calls}) == 2
    # The protected action itself never executed (parking pre-empts it);
    # only the plain step produced a side effect, exactly once.
    assert len(stub.side_effects) == 1


async def test_child_rejection_converges_parent_fail_policy(harness) -> None:
    """on_failure=fail: a rejected child converges the parent as failed with
    a distinguishable source — never a timeout wait."""

    parent_uuid, parent_ref, _parked, worker, stub = await _submit_workflow(harness)
    _ = _parked
    child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)
    child_record = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    child_wait = child_record.extra["wait"]
    ref = child_wait["contract_ref"]["approval_ref"]

    await _decide(harness, ref, approved=False, reason="not approved")
    issued = await _issue_resume(harness, child_id, child_wait["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED

    parent_final = await _wait_for_state(harness, parent_ref, "failed", timeout=60.0, worker=worker)
    assert parent_final.lifecycle_state.value == "failed"
    parent_result = await _task_result(harness, parent_ref)
    assert "approval_rejected" in str(parent_result.get("error")), parent_result
    # The child itself failed with the rejection category, zero executions.
    child_final = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_final.extra["result"]["error"] == "approval_rejected"
    assert [call for call in stub.calls if call["task"] == str(child_id)] == [
        {"task": str(child_id), "approval_wait": True}
    ]
    assert stub.side_effects == []


async def test_child_rejection_converges_parent_await_policy(harness) -> None:
    """on_failure=await: rejection routes the parent to reconciliation."""

    parent_uuid, parent_ref, _parked, worker, _stub = await _submit_workflow(harness, on_failure="await")
    child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)
    child_record = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    child_wait = child_record.extra["wait"]
    ref = child_wait["contract_ref"]["approval_ref"]

    await _decide(harness, ref, approved=False, reason="hold")
    issued = await _issue_resume(harness, child_id, child_wait["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED
    parent_final = await _wait_for_state(
        harness, parent_ref, "reconciliation_required", "failed", timeout=60.0, worker=worker
    )
    assert parent_final.lifecycle_state.value == "reconciliation_required"


async def test_forged_callback_while_child_waiting_rejected(harness) -> None:
    """A callback claiming success while the child is still parked is
    refused by the existing verification semantics; the parent is unchanged."""

    parent_uuid, parent_ref, _parked, worker, _stub = await _submit_workflow(harness)
    child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)
    child_record = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    parent_wait = (await asyncio.to_thread(harness["store"].get_task_state, parent_ref)).extra.get("wait") or {}

    async with harness["session_factory"]() as db:
        issued = await _service(harness, db, worker=worker).submit_workflow_callback(
            workspace_id=WS,
            task_id=parent_uuid,
            command_id="forged-callback-1",
            wait_token=parent_wait["wait_token"],
            child_task_id=child_id,
            declared_state="succeeded",
            payload={"step_index": 0},
            result_summary={"content": "totally done"},
        )
    assert issued.record.state is CommandState.REJECTED
    parent_state = await asyncio.to_thread(harness["store"].get_task_state, parent_ref)
    assert parent_state.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    fresh_wait = parent_state.extra["wait"]
    assert fresh_wait.get("consumed") is False
    assert fresh_wait.get("wait_token") == parent_wait.get("wait_token")


async def test_system_child_snapshot_uses_derived_tenant_key(harness) -> None:
    """The parked system child's snapshot address is the agent-principal
    derived key, and the resume gate derives the SAME key."""

    parent_uuid, _parent_ref, _parked, worker, stub = await _submit_workflow(harness)
    child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)
    child_record = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    child_wait = child_record.extra["wait"]

    # The stub saved under the derived key (the production save side uses
    # the same derivation); the child has no human user.
    detail = await _task_detail(harness, child_id)
    engine_session = uuid.UUID(detail["runs"][-1]["backend_ref"]["id"])
    from hecate.core.composition.entry_assembly import get_shared_session_state_store

    derived = system_execution_tenant(harness["agent_id"])
    saved = await get_shared_session_state_store().load(derived, derived, engine_session)
    assert saved is not None, "system child snapshot missing under the derived tenant key"

    ref = child_wait["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="approved")
    issued = await _issue_resume(harness, child_id, child_wait["wait_token"], worker, payload={"approval_ref": ref})
    assert issued.record.state is CommandState.APPLIED
    # The wake resumed natively (the gate loaded the snapshot) — the child
    # completes and the parent advances.
    await _wait_for_state(harness, child_ref, "succeeded", timeout=30.0)
    assert stub.calls[-1]["approval_wait"] is True


def test_system_tenant_normalizes_agent_uuid() -> None:
    agent = uuid.uuid4()
    assert system_execution_tenant(agent) == system_execution_tenant(str(agent).upper())
    assert system_execution_tenant(None) is None
    assert system_execution_tenant("unverified-agent") is None


@pytest.mark.parametrize("on_failure,expected", [("fail", "failed"), ("await", "reconciliation_required")])
async def test_rejected_child_callback_retries_after_projection(harness, monkeypatch, on_failure, expected):
    """A committed rejection survives a missed callback and relay restart."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector

    parent_id, parent_ref, _, worker, _ = await _submit_workflow(harness, on_failure=on_failure)
    child_id, child_ref = await _find_child(harness, parent_id, 0, worker=worker)
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    wait = child_state.extra["wait"]
    await _decide(harness, wait["contract_ref"]["approval_ref"], approved=False, reason="reject")

    async def lost_callback(self, *args):
        pass

    monkeypatch.setattr(TaskControlService, "_converge_workflow_parent_on_rejection", lost_callback)
    issued = await _issue_resume(harness, child_id, wait["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED
    parent = await asyncio.to_thread(harness["store"].get_task_state, parent_ref)
    assert parent.lifecycle_state is TaskLifecycleState.WAITING_INPUT

    callback = TaskControlService.submit_workflow_callback
    calls = []

    async def unavailable(self, **kwargs):
        calls.append(kwargs["command_id"])
        raise RuntimeError("callback transport unavailable after projection")

    monkeypatch.setattr(TaskControlService, "submit_workflow_callback", unavailable)
    relay = OutboxRelay(
        harness["store"].session_factory,
        PlatformOutboxProjector(harness["store"], harness["session_factory"]),
        relay_key="review-rejected-child",
    )
    await relay.pump_once()
    assert calls, "the committed rejection must be delivered by the outbox"
    assert relay.status()["lag"] > 0
    parent = await asyncio.to_thread(harness["store"].get_task_state, parent_ref)
    assert parent.lifecycle_state is TaskLifecycleState.WAITING_INPUT

    monkeypatch.setattr(TaskControlService, "submit_workflow_callback", callback)
    restarted = OutboxRelay(
        harness["store"].session_factory,
        PlatformOutboxProjector(harness["store"], harness["session_factory"]),
        relay_key="review-rejected-child",
    )
    assert await restarted.pump_once() > 0
    final = await _wait_for_state(harness, parent_ref, expected, worker=worker)
    applied = final.extra["_workflow_callbacks"]["0"]
    assert applied["child_task_id"] == str(child_id)
    await restarted.pump_once()
    assert (await asyncio.to_thread(harness["store"].get_task_state, parent_ref)).extra["_workflow_callbacks"] == {
        "0": applied
    }


@pytest.mark.parametrize("owner", ["system", "human", "missing", "partial"])
async def test_production_snapshot_save_ownership(owner):
    """The production save uses the same key, preserving human ownership."""
    from hecate_runtime.ports import StubRuntimePort
    from hecate_runtime.session_state import InMemorySessionStateStore

    from hecate.studio.state.state import AgentState
    from hecate.studio.workflows.execution_service import WorkflowExecutionService

    store = InMemorySessionStateStore()
    service = WorkflowExecutionService(port=StubRuntimePort(), db=None, checkpoint_store=store)
    agent = uuid.uuid4() if owner != "missing" else None
    human = uuid.uuid4() if owner in ("human", "partial") else None
    session = uuid.uuid4()
    await service._persist_session_state(
        agent_state=AgentState(summary="saved"),
        session_id=session,
        agent_id=agent,
        org_id=human if owner == "human" else None,
        user_id=human,
    )
    tenant = human if owner in ("human", "partial") else system_execution_tenant(agent)
    if tenant is not None:
        saved = await store.load(tenant, tenant, session)
        assert (saved is not None) is (owner in ("system", "human"))
    if owner == "partial":
        derived = system_execution_tenant(agent)
        assert await store.load(derived, derived, session) is None


@pytest.mark.parametrize("ownership", ["different_agent", "missing_agent", "human"])
async def test_resume_refuses_mismatched_snapshot_ownership(harness, ownership):
    """An existing snapshot is never loaded through a different owner key."""
    from dataclasses import replace

    from hecate.execution.task_run_registry import TaskRunRegistry

    parent_id, _, _, worker, _ = await _submit_workflow(harness)
    child_id, child_ref = await _find_child(harness, parent_id, 0, worker=worker)
    dispatcher = _dispatcher(harness)
    payload = await asyncio.to_thread(harness["store"].get_task_input, child_ref)
    context = dispatcher._context_of(child_ref, payload)
    context = (
        replace(context, agent_id=uuid.uuid4())
        if ownership == "different_agent"
        else (
            replace(context, agent_id=None) if ownership == "missing_agent" else replace(context, user_id=uuid.uuid4())
        )
    )
    detail = await _task_detail(harness, child_id)
    async with harness["session_factory"]() as db:
        run = await TaskRunRegistry(db).get_run(uuid.UUID(detail["runs"][-1]["run_id"]), WS)
        assert await dispatcher._resume_engine_session(db, run, context, approval_wake=True) is None
        assert dispatcher._last_resume_refusal


@pytest.mark.parametrize("policy,expected", [("fail", "failed"), ("await", "reconciliation_required")])
async def test_invalid_child_output_after_approval_never_advances(harness, policy, expected):
    """A resumed child may finish yet violate its declared output contract."""
    parent_id, parent_ref, _, worker, stub = await _submit_workflow(harness, on_failure=policy)
    child_id, child_ref = await _find_child(harness, parent_id, 0, worker=worker)
    state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    wait = state.extra["wait"]
    await _decide(harness, wait["contract_ref"]["approval_ref"], approved=True, reason="approved")
    stub.resume_content = "action completed, but this is not JSON"
    issued = await _issue_resume(harness, child_id, wait["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED
    final = await _wait_for_state(harness, parent_ref, expected, worker=worker)
    assert final.extra["result"]["failure_source"] == "validation"
    assert set(final.extra["_workflow_callbacks"]) == {"0"}
    assert {call["task"] for call in stub.calls} == {str(child_id)}, "next step must not execute"
    assert stub.side_effects == []


# --- approval-wait expiry convergence (step6) -------------------------------------


async def test_expired_child_wait_converges_parent_fail_policy(harness) -> None:
    """A child whose approval wait expires converges the waiting parent
    through the same verified callback path as a rejection — the parent
    applies its declared on_failure policy without any user command."""

    from datetime import UTC, datetime, timedelta

    from hecate.execution.approval_wait_expiration import ApprovalWaitExpirer

    parent_uuid, parent_ref, _parked, worker, stub = await _submit_workflow(harness)
    child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)
    calls_before = len(stub.calls)

    expirer = ApprovalWaitExpirer(
        harness["store"], harness["session_factory"], clock=lambda: datetime.now(UTC) + timedelta(hours=2)
    )
    assert await expirer.expire_once() == 1

    child_final = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_final.lifecycle_state is TaskLifecycleState.FAILED
    assert child_final.extra["result"]["error_category"] == "approval_wait_expired"
    # Zero model/tool re-execution across the convergence.
    assert len(stub.calls) == calls_before

    parent_final = await _wait_for_state(harness, parent_ref, "failed", timeout=60.0, worker=worker)
    assert parent_final.lifecycle_state.value == "failed"
    parent_result = await _task_result(harness, parent_ref)
    assert "approval_wait_expired" in str(parent_result.get("error")), parent_result

    # A late approval decision + resume on the expired child cannot reopen
    # anything: the consumed token and terminal child stand.
    ref = child_final.extra["wait"]["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="too late")
    issued = await _issue_resume(
        harness, child_id, child_final.extra["wait"]["wait_token"], worker, payload={"approval_ref": ref}
    )
    assert issued.record.state is CommandState.REJECTED
    assert (
        await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    ).lifecycle_state is TaskLifecycleState.FAILED
    assert (
        await asyncio.to_thread(harness["store"].get_task_state, parent_ref)
    ).lifecycle_state is TaskLifecycleState.FAILED


async def test_expired_child_wait_converges_parent_await_policy(harness) -> None:
    """on_failure=await: the expired child routes the parent to reconciliation."""

    from datetime import UTC, datetime, timedelta

    from hecate.execution.approval_wait_expiration import ApprovalWaitExpirer

    parent_uuid, parent_ref, _parked, worker, _stub = await _submit_workflow(harness, on_failure="await")
    _child_id, child_ref = await _find_child(harness, parent_uuid, 0, worker=worker)

    expirer = ApprovalWaitExpirer(
        harness["store"], harness["session_factory"], clock=lambda: datetime.now(UTC) + timedelta(hours=2)
    )
    assert await expirer.expire_once() == 1
    parent_final = await _wait_for_state(
        harness, parent_ref, "reconciliation_required", "failed", timeout=60.0, worker=worker
    )
    assert parent_final.lifecycle_state.value == "reconciliation_required"


async def test_expiry_parent_callback_loss_is_recovered_from_outbox(harness, monkeypatch) -> None:
    """A lost direct callback never strands a parent after child expiry."""
    from datetime import UTC, datetime, timedelta

    from hecate_durable.worker import OutboxRelay

    from hecate.execution.approval_wait_expiration import ApprovalWaitExpirer
    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    parent_id, parent_ref, _, worker, stub = await _submit_workflow(harness)
    _, child_ref = await _find_child(harness, parent_id, 0, worker=worker)
    calls_before = len(stub.calls)
    original = PlatformTaskDispatcher._issue_child_callback
    callback_calls = 0

    async def lose_first(self, *args, **kwargs):
        nonlocal callback_calls
        callback_calls += 1
        if callback_calls == 1:
            raise RuntimeError("callback lost after child terminal commit")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_issue_child_callback", lose_first)
    expirer = ApprovalWaitExpirer(
        harness["store"], harness["session_factory"], clock=lambda: datetime.now(UTC) + timedelta(hours=2)
    )
    assert await expirer.expire_once() == 1
    assert (
        await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    ).lifecycle_state is TaskLifecycleState.FAILED
    assert (
        await asyncio.to_thread(harness["store"].get_task_state, parent_ref)
    ).lifecycle_state is TaskLifecycleState.WAITING_INPUT
    relay = OutboxRelay(
        harness["store"].session_factory,
        PlatformOutboxProjector(harness["store"], harness["session_factory"]),
        relay_key="expiry-callback-recovery",
    )
    await relay.pump_once()
    final = await _wait_for_state(harness, parent_ref, "failed", timeout=30, worker=worker)
    assert final.lifecycle_state is TaskLifecycleState.FAILED
    assert callback_calls >= 2
    assert len(stub.calls) == calls_before
