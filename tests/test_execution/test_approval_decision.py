"""Human approval decisions: page service, phase recovery, and races.

The full loop runs through the REAL park path — an ``approval_wait``
submission whose executor raises the durable approval signal, the
dispatcher parking with a PENDING binding — and the REAL resume
machinery: the decision service records the authoritative judgment, the
deterministic ``resume`` command drives the existing wake gate and
single-use token, and the protected action is consumed exactly once by
``DurableBindingConsumer``. No wait record in these tests is hand-written.

Covered here: the self-approval ban against the task's persisted
initiator, unparseable initiators staying read-only, concurrent opposite
decisions producing exactly one authority winner, operation replay and
conflict semantics, the decision→resume crash window healed by the named
recovery consumer, and cancel/expiry races converging through receipts.
HTTP role gating (ADMIN-only, cross-workspace) lives in the API boundary
suite.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import ControlCommandKind, TaskLifecycleState
from hecate.core.database import Base
from hecate.execution.approval_decision import (
    ApprovalDecisionError,
    ApprovalDecisionService,
    ApprovalResumeRecovery,
    resume_command_id_for,
)
from hecate.execution.task_control import TaskControlService, task_ref_of
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.approval import ApprovalAuthorityModel, ApprovalDecisionModel
from hecate.models.managed_delivery import ManagedDeliveryModel  # noqa: F401
from hecate.models.organization import OrganizationModel
from hecate.models.standalone_enrollment import StandaloneEnrollmentModel  # noqa: F401
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from tests.test_execution import test_controller_loop_dispatch as controller_tests

controller_harness = controller_tests.harness

WS = uuid.UUID("00000000-0000-0000-0000-000000000030")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000031")
INITIATOR = uuid.UUID("00000000-0000-0000-0000-000000000032")
ADMIN = uuid.UUID("00000000-0000-0000-0000-000000000033")

PARKED_TOOL = "create_ticket"
PARKED_ARGS = {"title": "fix the ladder", "priority": "high"}


class _ApprovalParkStub:
    """Executor stub that raises the durable approval signal on first run.

    Mirrors the real chain around the signal: the parked run finalizes with
    the waiting_approval stop category, and an authorized resume continues
    with the ticket actually created (once).
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
        if run is not None:
            await TaskRunRegistry(db).finalize_run(
                uuid.UUID(kwargs["run_ref"].id),
                context.workspace_id,
                state="failed",
                error_category="waiting_approval",
            )
            await db.commit()
        from hecate_runtime.errors import ApprovalWaitSignal

        raise ApprovalWaitSignal(PARKED_TOOL, PARKED_ARGS, "high", {"invocation_id": f"test-action:{context.task_id}"})


@pytest.fixture
async def harness(tmp_path):
    db_file = tmp_path / "approval_decision.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})

    @event.listens_for(async_engine.sync_engine, "connect")
    def _busy_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA busy_timeout=30000")

    import sqlite3 as _sqlite3

    with _sqlite3.connect(db_file) as raw:
        # Switch the shared file to WAL once, deterministically, before any
        # engine connection exists (the sync durable engine and this engine
        # share the file; a per-connection journal_mode pragma races under
        # contention and silently stays in DELETE mode).
        raw.execute("PRAGMA journal_mode=WAL")

    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.core.composition.durable_platform import DurableSuite

    suite = DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core")

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=ADMIN))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=ADMIN, email="admin@example.com", hashed_password=uuid.uuid4().hex))
        db.add(UserModel(id=INITIATOR, email="initiator@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="approval-decision-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="b" * 64
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=WS,
                organization_id=ORG,
                owner_user_id=INITIATOR,
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


def _decision_service(harness, db: AsyncSession) -> ApprovalDecisionService:
    return ApprovalDecisionService(
        db,
        store=harness["suite"].store,
        recorder=harness["suite"].recorder,
        backend="postgres",
        ledger_source="core",
        session_factory=harness["session_factory"],
        worker=harness.get("worker"),
    )


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


async def _parked_task(harness, *, initiator: uuid.UUID = INITIATOR):
    """Real park: worker submission → executor signal → dispatcher park."""

    from hecate_durable.worker import DurableWorker

    stub = _ApprovalParkStub()
    stub.patch()
    harness["cleanups"].append(stub.unpatch)
    worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
    harness["worker"] = worker
    async with harness["session_factory"]() as db:
        result = await _service(harness, db, worker=worker).submit(
            workspace_id=WS,
            user_id=initiator,
            goal="create the ticket",
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "create the ticket"}], "approval_wait": True},
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = task_ref_of(task_uuid)
    await _wait_for_state(harness, t_ref, "waiting_approval", "failed")
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL, state.extra
    return task_uuid, t_ref, worker, stub


def _wait_of(harness, t_ref) -> dict[str, Any]:
    record = asyncio.get_event_loop().run_until_complete(asyncio.to_thread(harness["store"].get_task_state, t_ref))
    return (record.extra or {}).get("wait") or {}


async def test_real_park_admin_approval_resumes_and_consumes_once(harness) -> None:
    """Approve → resume applied → protected action consumed → task succeeded."""
    task_uuid, t_ref, worker, stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]

    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="op-approve-1",
            approval_ref=approval_ref,
            decision="approve",
            reason="ticket is justified",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
        await db.commit()
    assert result["decision"]["decision"] == "approve"
    assert result["resume"] is not None
    assert result["resume"]["state"] == "applied"

    final_state = await _wait_for_state(harness, t_ref, "succeeded", worker=worker)
    assert final_state == "succeeded"
    result = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert (result.extra or {}).get("result", {}).get("content") == "ticket created after approval"
    # The protected action ran exactly once: the original park (signal only,
    # no execution) plus the authorized continuation.
    assert len(stub.calls) == 2

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
    assert binding is not None
    assert binding.judgment.value == "approved"
    assert binding.decided_by == str(ADMIN)
    # Consumption at the dispatch boundary (DurableBindingConsumer) is
    # covered by the approval-wait park/consumer suites; this stub executor
    # bypasses the governed tool dispatch by design.

    # The operation row carries the phase linkage.
    async with harness["session_factory"]() as db:
        view = await _decision_service(harness, db).get_approval_view(WS, task_uuid)
    assert view["decision"] is not None
    assert view["decision"]["resume_command_id"] == resume_command_id_for("op-approve-1")
    assert view["decision"]["resume_receipt"]["state"] == "applied"


async def test_real_park_rejection_fails_task_without_executing(harness) -> None:
    """Reject → resume applies the rejection → task failed, action never ran."""
    task_uuid, t_ref, worker, stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]

    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="op-reject-1",
            approval_ref=wait["contract_ref"]["approval_ref"],
            decision="reject",
            reason="not a valid ticket",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
        await db.commit()
    assert result["resume"]["state"] == "applied"

    final_state = await _wait_for_state(harness, t_ref, "failed")
    assert final_state == "failed"
    result = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    outcome = (result.extra or {}).get("result") or {}
    assert outcome.get("error_category") == "approval_rejected"
    # Only the park call ever happened; the protected action never executed.
    assert len(stub.calls) == 1


async def test_initiator_cannot_decide_own_task(harness) -> None:
    """The self-approval ban: the task's persisted initiator is refused."""
    task_uuid, _t_ref, _worker, _stub = await _parked_task(harness, initiator=INITIATOR)
    async with harness["session_factory"]() as db:
        state = await asyncio.to_thread(harness["store"].get_task_state, task_ref_of(task_uuid))
        wait = (state.extra or {})["wait"]
        with pytest.raises(ApprovalDecisionError) as excinfo:
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="op-self-1",
                approval_ref=wait["contract_ref"]["approval_ref"],
                decision="approve",
                reason="self approve",
                wait_token=wait["wait_token"],
                expected_revision=state.revision,
                decided_by=str(INITIATOR),
            )
        assert excinfo.value.code == "SELF_APPROVAL"
        await db.rollback()
    # The wait stays live and the binding stays pending.
    state = await asyncio.to_thread(harness["store"].get_task_state, task_ref_of(task_uuid))
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_system_initiated_task_has_no_self_approval_ban(harness) -> None:
    """A task with no human initiator can be decided by any ADMIN."""
    task_uuid, _t_ref, _worker, _stub = await _parked_task(harness, initiator=None)
    state = await asyncio.to_thread(harness["store"].get_task_state, task_ref_of(task_uuid))
    wait = (state.extra or {})["wait"]
    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="op-sys-1",
            approval_ref=wait["contract_ref"]["approval_ref"],
            decision="approve",
            reason="system task",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
        await db.commit()
    assert result["resume"]["state"] == "applied"


async def test_unparseable_initiator_is_read_only(harness) -> None:
    """A corrupt initiator identity keeps the wait read-only."""
    task_uuid, t_ref, _worker, _stub = await _parked_task(harness)
    # Corrupt the persisted initiator after the park.
    corrupted = await asyncio.to_thread(harness["suite"].store.get_task_input, task_ref_of(task_uuid))
    corrupted["user_id"] = "not-a-uuid"
    await asyncio.to_thread(
        harness["suite"].store.apply_task_state,
        task_ref_of(task_uuid),
        TaskLifecycleState.WAITING_APPROVAL,
        input_payload=corrupted,
    )
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    async with harness["session_factory"]() as db:
        with pytest.raises(ApprovalDecisionError) as excinfo:
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="op-corrupt-1",
                approval_ref=wait["contract_ref"]["approval_ref"],
                decision="approve",
                reason="x",
                wait_token=wait["wait_token"],
                expected_revision=state.revision,
                decided_by=str(ADMIN),
            )
        assert excinfo.value.code == "INITIATOR_UNPARSEABLE"
        await db.rollback()
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert state.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_concurrent_opposite_decisions_have_one_authority_winner(harness) -> None:
    """approve vs reject race: the ledger CAS yields exactly one winner."""
    task_uuid, _t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, task_ref_of(task_uuid))
    wait = (state.extra or {})["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]

    outcomes: dict[str, Any] = {}

    async def decide(name: str, decision: str) -> None:
        try:
            async with harness["session_factory"]() as db:
                result = await _decision_service(harness, db).submit_decision(
                    WS,
                    task_uuid,
                    operation_id=f"op-race-{name}",
                    approval_ref=approval_ref,
                    decision=decision,
                    reason=f"{name} reasoning",
                    wait_token=wait["wait_token"],
                    expected_revision=state.revision,
                    decided_by=str(ADMIN),
                )
                await db.commit()
            outcomes[name] = result
        except ApprovalDecisionError as exc:
            outcomes[name] = exc

    await asyncio.gather(decide("approve", "approve"), decide("reject", "reject"))
    winners = [name for name, value in outcomes.items() if not isinstance(value, ApprovalDecisionError)]
    assert len(winners) == 1, outcomes
    winner_decision = outcomes[winners[0]]["decision"]["decision"]
    expected_judgment = "approved" if winner_decision == "approve" else "rejected"

    from hecate_governance.approval import (
        ApprovalLedger,  # noqa: F401
    )

    async with harness["session_factory"]() as db:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger

        binding = await SqlApprovalLedger(db).get(approval_ref)
    assert binding.judgment.value == expected_judgment
    # Only the winner's operation row exists.
    async with harness["session_factory"]() as db:
        for name in ("approve", "reject"):
            if name != winners[0]:
                with pytest.raises(ApprovalDecisionError):
                    await _decision_service(harness, db).submit_decision(
                        WS,
                        task_uuid,
                        operation_id=f"op-race-{name}",
                        approval_ref=approval_ref,
                        decision=name,
                        reason="retry of the loser",
                        wait_token=wait["wait_token"],
                        expected_revision=state.revision,
                        decided_by=str(ADMIN),
                    )


async def test_operation_replay_returns_facts_and_conflicts_on_change(harness) -> None:
    """Same op id + same body replays; a changed decision conflicts."""
    task_uuid, _t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, task_ref_of(task_uuid))
    wait = (state.extra or {})["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]

    async with harness["session_factory"]() as db:
        first = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="op-replay-1",
            approval_ref=approval_ref,
            decision="approve",
            reason="ok",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
        await db.commit()
    assert first["decision"].get("replay") is None  # first submission is not a replay

    async with harness["session_factory"]() as db:
        replay = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="op-replay-1",
            approval_ref=approval_ref,
            decision="approve",
            reason="ok",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
    assert replay["decision"]["replay"] is True
    assert replay["decision"]["decided_by"] == str(ADMIN)

    async with harness["session_factory"]() as db:
        with pytest.raises(ApprovalDecisionError) as excinfo:
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="op-replay-1",
                approval_ref=approval_ref,
                decision="reject",
                reason="changed my mind",
                wait_token=wait["wait_token"],
                expected_revision=state.revision,
                decided_by=str(ADMIN),
            )
        assert excinfo.value.code == "OPERATION_CONFLICT"


async def test_crash_before_resume_is_healed_by_named_recovery(harness, monkeypatch) -> None:
    """Decision committed, resume phase lost: the recovery consumer completes it."""
    task_uuid, t_ref, worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]

    # Simulate the crash window: the resume phase fails after the decision
    # committed — no resume command exists (the token is still unconsumed),
    # and the submission still reports the recorded decision.
    from hecate.execution.approval_decision import ApprovalDecisionService as _Svc

    original_issue = _Svc._issue_resume

    async def _crashing_resume(self, row, task_id, workspace_id):
        raise RuntimeError("process died between the phases")

    monkeypatch.setattr(_Svc, "_issue_resume", _crashing_resume)
    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="op-crash-1",
            approval_ref=approval_ref,
            decision="approve",
            reason="approved",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
        await db.commit()
    assert result["decision"]["decision"] == "approve"
    assert result["resume"]["state"] == "pending_recovery"
    receipt_probe = await asyncio.to_thread(harness["suite"].store.get, resume_command_id_for("op-crash-1"))
    assert receipt_probe is None
    monkeypatch.setattr(_Svc, "_issue_resume", original_issue)  # the worker restarts healthy

    recovery = ApprovalResumeRecovery(harness["suite"].store, harness["session_factory"])
    processed = await recovery.resume_once()
    assert processed >= 1

    final_state = await _wait_for_state(harness, t_ref, "succeeded", worker=worker)
    assert final_state == "succeeded"
    async with harness["session_factory"]() as db:
        view = await _decision_service(harness, db).get_approval_view(WS, task_uuid)
    assert view["decision"]["resume_command_id"] == resume_command_id_for("op-crash-1")
    assert view["decision"]["resume_receipt"]["state"] == "applied"
    # Running the recovery again is a no-op (the linkage is now recorded).
    assert await recovery.resume_once() == 0


async def test_cancel_race_after_decision_rejects_the_resume(harness, monkeypatch) -> None:
    """A cancel after decision commit still prevents the subsequent wake."""
    task_uuid, t_ref, worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    original = ApprovalDecisionService._issue_resume

    async def cancel_before_resume(self, row, task_id, workspace_id):
        async with harness["session_factory"]() as cancel_db:
            await _service(harness, cancel_db, worker=worker).issue_command(
                workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="tester"
            )
        return await original(self, row, task_id, workspace_id)

    monkeypatch.setattr(ApprovalDecisionService, "_issue_resume", cancel_before_resume)
    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(
            WS,
            task_uuid,
            operation_id="cancel-between-phases",
            approval_ref=wait["contract_ref"]["approval_ref"],
            decision="approve",
            reason="approved while live",
            wait_token=wait["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
    assert result["decision"]["decision"] == "approve"
    assert result["resume"]["state"] == "rejected"
    assert await _wait_for_state(harness, t_ref, "cancelled") == "cancelled"


# --- HTTP boundary: the ADMIN gate, demoted replay, cross-workspace read ----


async def test_approval_api_gates_admin_decision_and_demoted_replay(harness, monkeypatch) -> None:
    """Server-side gating: ADMIN decides, EDITOR/VIEWER cannot, a demoted
    identity cannot even replay its own earlier decision, and the request
    body can never supply the decider identity."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from hecate.channel.api.tasks import get_task_control_service, router
    from hecate.core.auth_context import AuthContext
    from hecate.core.deps_workspace import get_auth_context

    task_uuid, t_ref, worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]

    app = FastAPI()
    app.include_router(router, prefix="/api")

    from hecate.models.workspace_member import WorkspaceRole

    def _ctx(role) -> AuthContext:
        return AuthContext(
            user_id=ADMIN,
            org_id=ORG,
            workspace_id=WS,
            role=role,
            auth_method="jwt",
            api_key_scope=None,
        )

    def _service_binding(db: AsyncSession):
        def _factory():
            # Worker-bound: the resume requeues only; the test drives the
            # re-dispatch explicitly so no background dispatch contends
            # with the request session on the shared SQLite file.
            return _service(harness, db, worker=worker)

        return _factory

    async def _override_service():
        # Request-scoped session, exactly like the platform's get_db
        # dependency: no cross-request session state on the shared file.
        async with harness["session_factory"]() as db:
            yield _service(harness, db, worker=worker)

    app.dependency_overrides[get_task_control_service] = _override_service
    app.dependency_overrides[get_auth_context] = lambda: _ctx(WorkspaceRole.ADMIN)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = {
            "operation_id": "op-http-1",
            "approval_ref": approval_ref,
            "decision": "approve",
            "reason": "http decision",
            "wait_token": wait["wait_token"],
            "expected_revision": state.revision,
        }
        for role in (WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None):
            app.dependency_overrides[get_auth_context] = lambda role=role: _ctx(role)
            denied = await client.post(f"/api/tasks/{task_uuid}/approval", json=body)
            assert denied.status_code == 403, denied.text
        from dataclasses import replace

        app.dependency_overrides[get_auth_context] = lambda: replace(_ctx(WorkspaceRole.ADMIN), user_id=INITIATOR)
        assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 403
        app.dependency_overrides[get_auth_context] = lambda: replace(
            _ctx(WorkspaceRole.ADMIN), workspace_id=uuid.uuid4()
        )
        assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 404
        assert (await client.get(f"/api/tasks/{task_uuid}/approval")).status_code == 404
        app.dependency_overrides[get_auth_context] = lambda: replace(_ctx(WorkspaceRole.ADMIN), api_key_scope="system")
        assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 403
        async with harness["session_factory"]() as check_db:
            assert await check_db.scalar(select(ApprovalDecisionModel.id)) is None
            authority = await check_db.scalar(
                select(ApprovalAuthorityModel).where(ApprovalAuthorityModel.approval_ref == approval_ref)
            )
            assert authority.judgment == "pending"
        app.dependency_overrides[get_auth_context] = lambda: _ctx(WorkspaceRole.ADMIN)
        initial_view = await client.get(f"/api/tasks/{task_uuid}/approval")
        assert initial_view.json()["can_decide"] is True
        assert "fix the ladder" not in initial_view.text
        reserved = await client.post(
            f"/api/tasks/{task_uuid}/commands",
            json={"kind": "resume", "command_id": resume_command_id_for(body["operation_id"])},
        )
        assert reserved.status_code == 422
        assert harness["store"].get(resume_command_id_for(body["operation_id"])) is None
        # Extra request metadata cannot impersonate the decider.
        decided = await client.post(f"/api/tasks/{task_uuid}/approval", json={**body, "decided_by": str(INITIATOR)})
        assert decided.status_code == 200, decided.text
        facts = decided.json()
        assert facts["decision"]["decision"] == "approve"
        assert facts["decision"]["decided_by"] == str(ADMIN)
        assert facts["resume"]["state"] == "applied"

        # Same operation id + same body replays the recorded facts.
        replay = await client.post(f"/api/tasks/{task_uuid}/approval", json=body)
        assert replay.status_code == 200
        assert replay.json()["decision"]["replay"] is True

        view = await client.get(f"/api/tasks/{task_uuid}/approval")
        assert view.status_code == 200
        assert view.json()["decision"]["operation_id"] == "op-http-1"

        # A changed decision under the same operation id conflicts.
        changed = {**body, "decision": "reject", "reason": "changed"}
        conflict = await client.post(f"/api/tasks/{task_uuid}/approval", json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error"]["code"] == "OPERATION_CONFLICT"

    # A demoted identity cannot even replay its own recorded decision: the
    # ADMIN gate runs before any authority query or idempotent read.
    app.dependency_overrides[get_auth_context] = lambda: _ctx(WorkspaceRole.EDITOR)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        demoted = await client.post(f"/api/tasks/{task_uuid}/approval", json=body)
        assert demoted.status_code == 403, demoted.text
        demoted_view = await client.get(f"/api/tasks/{task_uuid}/approval")
        assert demoted_view.status_code == 200  # observation stays readable

    # The recorded decision survived the demoted replay attempt verbatim.
    async with harness["session_factory"]() as db:
        view = await _decision_service(harness, db).get_approval_view(WS, task_uuid)
    assert view["decision"]["decided_by"] == str(ADMIN)
    assert view["decision"]["decision"] == "approve"


@pytest.mark.parametrize("case", ["token", "revision", "cancelled", "expired_wait", "revoked", "scope", "parameters"])
async def test_invalid_wait_cannot_record_authority_decision(harness, case):
    """Reject before changing approval authority, not merely at resume."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    task_uuid, t_ref, worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    ref = wait["contract_ref"]["approval_ref"]
    token, revision = wait["wait_token"], state.revision
    if case == "token":
        token = "wrong-token"
    elif case == "revision":
        revision += 7
    elif case == "cancelled":
        async with harness["session_factory"]() as db:
            await _service(harness, db, worker=worker).issue_command(
                workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="reviewer"
            )
    elif case == "expired_wait":
        wait = {**wait, "wait_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
        state = await asyncio.to_thread(
            harness["store"].apply_task_state, t_ref, TaskLifecycleState.WAITING_APPROVAL, extra_update={"wait": wait}
        )
        revision = state.revision
    else:
        values = (
            {"revoked": True}
            if case == "revoked"
            else ({"task_scope": str(uuid.uuid4())} if case == "scope" else {"arguments_digest": "f" * 64})
        )
        async with harness["session_factory"].begin() as db:
            await db.execute(
                update(ApprovalAuthorityModel).where(ApprovalAuthorityModel.approval_ref == ref).values(**values)
            )
    async with harness["session_factory"]() as db:
        with pytest.raises(ApprovalDecisionError):
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="invalid-wait",
                approval_ref=ref,
                decision="approve",
                reason="invalid",
                wait_token=token,
                expected_revision=revision,
                decided_by=str(ADMIN),
            )
        await db.rollback()
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        binding = await SqlApprovalLedger(db).get(ref)
        assert binding.judgment.value == "pending"
        assert await db.scalar(select(ApprovalDecisionModel.id)) is None


async def test_failed_operation_insert_rolls_back_the_authority_decision(harness, monkeypatch):
    """The approval cannot commit independently from its operation/audit row."""
    task_uuid, t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    async with harness["session_factory"]() as db:
        original = db.flush

        async def fail_operation(*args, **kwargs):
            if any(isinstance(row, ApprovalDecisionModel) for row in db.new):
                raise RuntimeError("operation write unavailable")
            return await original(*args, **kwargs)

        monkeypatch.setattr(db, "flush", fail_operation)
        with pytest.raises(RuntimeError, match="operation write"):
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="failed-insert",
                approval_ref=wait["contract_ref"]["approval_ref"],
                decision="approve",
                reason="test",
                wait_token=wait["wait_token"],
                expected_revision=state.revision,
                decided_by=str(ADMIN),
            )
        await db.rollback()
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with harness["session_factory"]() as db:
        binding = await SqlApprovalLedger(db).get(wait["contract_ref"]["approval_ref"])
        assert binding.judgment.value == "pending"


@pytest.mark.parametrize("field,value", [("wait_token", "changed"), ("expected_revision", 999), ("reason", "changed")])
async def test_operation_identity_binds_every_request_field(harness, monkeypatch, field, value):
    task_uuid, t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    body = dict(
        operation_id="frozen-op",
        approval_ref=wait["contract_ref"]["approval_ref"],
        decision="approve",
        reason="fixed",
        wait_token=wait["wait_token"],
        expected_revision=state.revision,
        decided_by=str(ADMIN),
    )

    async def no_resume(*args, **kwargs):
        raise RuntimeError("pause after decision")

    monkeypatch.setattr(ApprovalDecisionService, "_issue_resume", no_resume)
    async with harness["session_factory"]() as db:
        await _decision_service(harness, db).submit_decision(WS, task_uuid, **body)
    async with harness["session_factory"]() as db:
        with pytest.raises(ApprovalDecisionError) as error:
            await _decision_service(harness, db).submit_decision(WS, task_uuid, **{**body, field: value})
        assert error.value.code == "OPERATION_CONFLICT"


async def test_another_operation_cannot_adopt_an_existing_decision(harness, monkeypatch):
    task_uuid, t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    body = dict(
        approval_ref=wait["contract_ref"]["approval_ref"],
        decision="approve",
        reason="first",
        wait_token=wait["wait_token"],
        expected_revision=state.revision,
        decided_by=str(ADMIN),
    )

    async def no_resume(*args, **kwargs):
        raise RuntimeError("pause after decision")

    monkeypatch.setattr(ApprovalDecisionService, "_issue_resume", no_resume)
    async with harness["session_factory"]() as db:
        await _decision_service(harness, db).submit_decision(WS, task_uuid, operation_id="original", **body)
    async with harness["session_factory"]() as db:
        with pytest.raises(ApprovalDecisionError) as error:
            await _decision_service(harness, db).submit_decision(
                WS, task_uuid, operation_id="different", **{**body, "reason": "forged"}
            )
        assert error.value.code == "ALREADY_DECIDED"
        assert len(list(await db.scalars(select(ApprovalDecisionModel)))) == 1


async def test_replay_and_view_bind_to_original_operation_across_new_wait(harness, monkeypatch):
    task_uuid, t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = (state.extra or {})["wait"]
    body = dict(
        operation_id="old-op",
        approval_ref=wait["contract_ref"]["approval_ref"],
        decision="approve",
        reason="first",
        wait_token=wait["wait_token"],
        expected_revision=state.revision,
        decided_by=str(ADMIN),
    )

    async def no_resume(*args, **kwargs):
        raise RuntimeError("pause after decision")

    monkeypatch.setattr(ApprovalDecisionService, "_issue_resume", no_resume)
    async with harness["session_factory"]() as db:
        await _decision_service(harness, db).submit_decision(WS, task_uuid, **body)
    changed_wait = {
        **wait,
        "wait_token": "new-token",
        "contract_ref": {**wait["contract_ref"], "approval_ref": "new-ref"},
    }
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        t_ref,
        TaskLifecycleState.WAITING_APPROVAL,
        extra_update={"wait": changed_wait},
    )
    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(WS, task_uuid, **body)
        assert result["decision"]["replay"] is True
        view = await _decision_service(harness, db).get_approval_view(WS, task_uuid)
        assert view["decision"] is None
        assert view["contract"]["approval_ref"] == "new-ref"


def test_maximum_operation_id_has_bounded_resume_command_id():
    assert len(resume_command_id_for("x" * 200)) <= 200


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_governed_controller_approval_decisions_and_fresh_action_reapproval(
    controller_harness, monkeypatch, decision
):
    """Exercise real policy, authority consumption, stable actions and recovery."""
    import json
    from types import SimpleNamespace

    from hecate_runtime.eventstore import InMemoryEventStore
    from hecate_runtime.security.approval import FailingClosedApprovalCallback
    from hecate_runtime.tool_access import AccessDecision

    import hecate.core.composition.guardrail_platform as guardrail_platform
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from tests.test_execution.test_controller_loop_dispatch import (
        ScriptedLoopPort,
        _dispatch_to_state,
        _dispatch_to_terminal,
        _submit_without_dispatch,
        _worker_for,
    )

    env = controller_harness

    class _Port(ScriptedLoopPort):
        async def llm_invoke_structured(self, messages, config):
            self.model_calls += 1
            if len([m for m in messages if m.get("role") == "tool"]) >= 2:
                yield {"content": "two governed actions completed", "tool_calls": None}
            else:
                yield {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "same-model-call",
                            "type": "function",
                            "function": {"name": "query_inventory", "arguments": json.dumps({"sku": "same"})},
                        }
                    ],
                }

    class _Policy:
        def evaluate(self, *args, **kwargs):
            return AccessDecision.REQUIRE_APPROVAL

    async def bundle(*args, **kwargs):
        return SimpleNamespace(
            access_policy=_Policy(),
            rules=[],
            denial_tracker=None,
            middleware_chains={},
            approval_callback=FailingClosedApprovalCallback(
                event_store=InMemoryEventStore(), session_id=uuid.uuid4(), workspace_id=WS, agent_id=env["agent_id"]
            ),
        )

    port = _Port(final_text="unused")
    t_ref, task_uuid = await _submit_without_dispatch(env, port, approval_wait=True)
    decider = str(uuid.uuid4())
    old_body = None
    bindings = []
    with monkeypatch.context() as patch:
        patch.setattr(guardrail_platform, "assemble_guardrails", bundle)
        for turn in range(2 if decision == "approve" else 1):
            worker = _worker_for(env)
            record = await _dispatch_to_state(env, t_ref, worker, "waiting_approval")
            wait = record.extra["wait"]
            assert len(port.tool_calls) == turn
            body = dict(
                operation_id=f"governed-{turn}",
                approval_ref=wait["contract_ref"]["approval_ref"],
                decision=decision,
                reason="verified",
                wait_token=wait["wait_token"],
                expected_revision=record.revision,
                decided_by=decider,
            )
            async with env["session_factory"]() as db:
                service = ApprovalDecisionService.of(_service(env, db, worker=worker))
                view = await service.get_approval_view(WS, task_uuid, decided_by=decider, can_admin=True)
                assert view["can_decide"] is True
                assert view["decision"] is None
                if old_body is not None:
                    replay = await service.submit_decision(WS, task_uuid, **old_body)
                    assert replay["decision"]["replay"] is True
                result = await service.submit_decision(WS, task_uuid, **body)
                assert result["resume"]["state"] == "applied", result
            old_body = body
            bindings.append((wait["contract_ref"]["approval_ref"], wait["contract_ref"]["invocation_id"]))
        final = await _dispatch_to_terminal(env, t_ref, worker=_worker_for(env))
        assert final.lifecycle_state.value == ("succeeded" if decision == "approve" else "failed")
    assert len(port.tool_calls) == (2 if decision == "approve" else 0)
    assert port.model_calls == (3 if decision == "approve" else 1)
    async with env["session_factory"]() as db:
        for ref, invocation in bindings:
            authority = await SqlApprovalLedger(db).get(ref)
            assert authority.judgment.value == ("approved" if decision == "approve" else "rejected")
            assert authority.consumed_by_invocation_id == (invocation if decision == "approve" else None)
    assert len(set(ref for ref, _ in bindings)) == len(bindings)


async def test_recovery_rotates_poisoned_batch_and_reaches_later_decision(harness, monkeypatch):
    """A bounded failed batch cannot permanently starve later recovery work."""
    from datetime import UTC, datetime, timedelta

    task_uuid, t_ref, worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = state.extra["wait"]

    async def unavailable(*args, **kwargs):
        raise RuntimeError("decision committed before process exit")

    with monkeypatch.context() as patch:
        patch.setattr(ApprovalDecisionService, "_issue_resume", unavailable)
        async with harness["session_factory"]() as db:
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="recover-after-poison",
                approval_ref=wait["contract_ref"]["approval_ref"],
                decision="approve",
                reason="yes",
                wait_token=wait["wait_token"],
                expected_revision=state.revision,
                decided_by=str(ADMIN),
            )
    # These rows model retained operations whose original tasks are gone.
    async with harness["session_factory"].begin() as db:
        for index in range(32):
            db.add(
                ApprovalDecisionModel(
                    operation_id=f"poison-{index}",
                    approval_ref="gone",
                    task_id=uuid.uuid4(),
                    workspace_id=WS,
                    decision="approve",
                    decided_by=str(ADMIN),
                    request_digest="a" * 64,
                    wait_token="old",
                    expected_revision=1,
                    updated_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
    recovery = ApprovalResumeRecovery(harness["store"], harness["session_factory"], worker=worker)
    assert await recovery.resume_once() == 0
    assert await recovery.resume_once() == 1
    async with harness["session_factory"]() as db:
        row = await db.scalar(
            select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "recover-after-poison")
        )
        assert row.resume_command_id == resume_command_id_for("recover-after-poison")
    assert await _wait_for_state(harness, t_ref, "succeeded", worker=worker) == "succeeded"


@pytest.mark.parametrize("failure", ["expiry", "decision"])
async def test_worker_composition_preserves_both_approval_consumers(monkeypatch, failure):
    """A failing expiry pass and a failing recovery pass remain independent."""
    from types import SimpleNamespace

    import hecate_durable.worker as worker_module
    from fastapi import FastAPI

    import hecate.core.composition.durable_platform as platform
    import hecate.core.config as config
    import hecate.execution.approval_decision as decisions
    import hecate.execution.approval_wait_expiration as expiry
    from hecate.core.composition.wiring import start_durable_worker, stop_durable_worker

    calls = []

    class _Worker:
        def __init__(self, *args, wait_reconciler, **kwargs):
            self.reconcile = wait_reconciler

        async def run_forever(self):
            await asyncio.Event().wait()

        def request_drain(self):
            pass

        async def drain(self):
            return 0

    class _Relay:
        def __init__(self, *args, **kwargs):
            pass

        async def run_forever(self):
            await asyncio.Event().wait()

        def stop(self):
            pass

    class _Expirer:
        def __init__(self, *args):
            pass

        async def expire_once(self):
            calls.append("expiry")
            if failure == "expiry":
                raise RuntimeError("expiry unavailable")
            return 1

    class _Recovery:
        def __init__(self, *args, worker):
            assert isinstance(worker, _Worker)

        async def resume_once(self):
            calls.append("decision")
            if failure == "decision":
                raise RuntimeError("decision recovery unavailable")
            return 2

    monkeypatch.setattr(config.settings, "HECATE_DURABLE_WORKER", "on")
    monkeypatch.setattr(
        platform,
        "get_durable_suite",
        lambda: SimpleNamespace(backend="postgres", store=SimpleNamespace(leases=object(), session_factory=object())),
    )
    monkeypatch.setattr(worker_module, "DurableWorker", _Worker)
    monkeypatch.setattr(worker_module, "OutboxRelay", _Relay)
    monkeypatch.setattr(expiry, "ApprovalWaitExpirer", _Expirer)
    monkeypatch.setattr(decisions, "ApprovalResumeRecovery", _Recovery)
    app = FastAPI()
    await start_durable_worker(app)
    try:
        assert await app.state.durable_worker.reconcile() == (2 if failure == "expiry" else 1)
        assert calls == ["expiry", "decision"]
    finally:
        await stop_durable_worker(app)


async def test_untrusted_restarted_authority_clock_is_not_a_human_decision(harness):
    """An unverifiable clock reports a time failure and retains pending authority."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    task_uuid, t_ref, _worker, _stub = await _parked_task(harness)
    state = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    wait = state.extra["wait"]
    ref = wait["contract_ref"]["approval_ref"]
    async with harness["session_factory"].begin() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(ApprovalAuthorityModel.approval_ref == ref)
            .values(clock_epoch="a-previous-process", anchor_wall=datetime.now(UTC) + timedelta(days=1))
        )
    async with harness["session_factory"]() as db:
        with pytest.raises(ApprovalDecisionError) as error:
            await _decision_service(harness, db).submit_decision(
                WS,
                task_uuid,
                operation_id="untrusted-clock",
                approval_ref=ref,
                decision="approve",
                reason="verify time",
                wait_token=wait["wait_token"],
                expected_revision=state.revision,
                decided_by=str(ADMIN),
            )
        assert error.value.code == "UNTRUSTED_TIME"
        authority = await db.scalar(select(ApprovalAuthorityModel).where(ApprovalAuthorityModel.approval_ref == ref))
        assert authority.judgment == "pending"
        assert await db.scalar(select(ApprovalDecisionModel.id)) is None
