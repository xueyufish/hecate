"""Durable approval wait across a REAL process restart (step5e).

The park, the wake authorization, and the resume are driven through two
REAL worker subprocesses against one PostgreSQL: process A executes to the
``WAITING_APPROVAL`` park and is then killed; the control plane records the
approval decision and issues ``resume``; process B (fresh interpreter,
fresh assembly, same database) claims the task, passes the resumption gate,
consumes the approved binding once, and drives the run to its terminal.
Only the provider boundary is scripted (same doubles as the G3 entry
tests); the side-effect counter is a shared file so the single-execution
claim is observed across process boundaries.

Without ``HECATE_STEP6_POSTGRES_URL`` this module reports an explicit skip
— a skip is never counted as a pass.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import CommandState, ControlCommandKind
from hecate.contracts.execution.references import task_ref as make_task_ref

PG_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")

pytestmark = [
    pytest.mark.skipif(not PG_URL, reason="HECATE_STEP6_POSTGRES_URL not configured; restart e2e reports skip"),
    pytest.mark.timeout(420),
]

_REPO = Path(__file__).resolve().parents[2]


async def _create_database(base_url: str, name: str) -> str:
    """One fresh database per run on the container (asyncpg cannot create DBs)."""

    admin_url = (
        make_url(base_url).set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    )
    import psycopg

    def _create() -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))

    await asyncio.to_thread(_create)
    return base_url.rsplit("/", 1)[0] + "/" + name


@pytest.fixture
async def restart_stack(tmp_path: Path, request):
    """Unique database + durable store + seeded tenant/agent on the container."""

    db_name = "e2e_appr_" + uuid.uuid4().hex[:10]
    mode = getattr(request, "param", "normal")
    async_url = await _create_database(PG_URL.replace("+psycopg", "+asyncpg"), db_name)
    sync_url = async_url.replace("+asyncpg", "+psycopg")

    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(sync_url, source="platform")
    store.create_schema()

    engine = create_async_engine(async_url)
    from hecate.studio.event_state import models as event_state_models  # noqa: F401
    from hecate.studio.session_state import models as session_state_models  # noqa: F401
    from tests.conftest import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel
    from hecate.models.organization import OrganizationModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel

    org_id = uuid.UUID(int=1)
    ws_id = uuid.UUID(int=2)
    user_id = uuid.UUID(int=3)
    async with factory() as db:
        db.add(OrganizationModel(id=org_id, name="e2e-org", slug="e2e-org", owner_id=user_id))
        db.add(WorkspaceModel(id=ws_id, org_id=org_id, name="e2e-ws", slug="e2e-ws"))
        db.add(UserModel(id=user_id, email="e2e@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=ws_id, name="approval-e2e-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={
                "tools": ["stub_ticket", *(["stub_prepare"] if mode == "precompleted" else [])],
                "model_config": {
                    "model": os.environ.get("BASELINE_MODEL", "stub-model")
                    if os.environ.get("HECATE_E2E_REAL_MODEL") == "1"
                    else "stub-model"
                },
            },
            content_hash="a" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=ws_id,
                organization_id=org_id,
                owner_user_id=user_id,
            )
        )
        from hecate.models.tool_policy import ToolPolicyModel

        db.add(
            ToolPolicyModel(
                workspace_id=ws_id,
                rule_action="ask",
                tool_pattern="stub_ticket",
                priority=10,
                description="e2e: protected action requires approval",
            )
        )
        if mode == "precompleted":
            db.add(
                ToolPolicyModel(
                    workspace_id=ws_id,
                    rule_action="allow",
                    tool_pattern="stub_prepare",
                    priority=20,
                    description="synthetic pre-approval completed action",
                )
            )
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=ws_id,
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

    counter_file = tmp_path / "side_effects.jsonl"
    counter_file.write_text("", encoding="utf-8")
    state = {
        "async_url": async_url,
        "sync_url": sync_url,
        "factory": factory,
        "store": store,
        "engine": engine,
        "ws_id": ws_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "counter_file": str(counter_file),
        "model_trace_file": str(tmp_path / "model_trace.jsonl"),
        "consumed_file": str(tmp_path / "consumed.txt"),
        "mode": mode,
        "workers": [],
    }
    try:
        yield state
    finally:
        for process in state["workers"]:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 15)
        for handle, _path in state.get("worker_logs", []):
            handle.close()
        await engine.dispose()
        store.dispose()
        sync_admin = (
            make_url(PG_URL).set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
        )
        import psycopg

        def _drop():
            with psycopg.connect(sync_admin, autocommit=True) as conn:
                conn.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(db_name)))

        await asyncio.to_thread(_drop)


class _RequeueWorker:
    """Truthy stand-in: the control plane requeues only, a child executes.

    The real fleet worker claims queued tasks on its own cycle; the parent
    process never executes business dispatches in this E2E.
    """

    async def dispatch_once(self, task_ref) -> bool:
        return False


def _service(stack: dict[str, Any], db: AsyncSession):
    from hecate.execution.task_control import TaskControlService

    return TaskControlService(
        db,
        store=stack["store"],
        recorder=stack["store"],
        backend="postgres",
        ledger_source="core",
        session_factory=stack["factory"],
        worker=_RequeueWorker(),
    )


def _child_env(stack: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": stack["async_url"],
            "HECATE_E2E_DURABLE_DSN": stack["sync_url"],
            "HECATE_E2E_COUNTER_FILE": stack["counter_file"],
            "HECATE_E2E_LEASE_TTL": stack.get("lease_ttl", "5"),
            "HECATE_E2E_MODE": stack["mode"],
            "HECATE_E2E_MODEL_TRACE_FILE": stack["model_trace_file"],
            "HECATE_E2E_CONSUMED_FILE": stack["consumed_file"],
            "SESSION_STATE_STORE_BACKEND": "postgres",
            "PYTHONPATH": str(_REPO),
        }
    )
    # The deterministic variant always runs the scripted provider; only the
    # real-model test opts its children into the real llm service.
    if not stack.get("real_model"):
        env.pop("HECATE_E2E_REAL_MODEL", None)
    env.pop("HECATE_E2E_CRASH_AFTER_CONSUME", None)
    if stack.get("crash_after_consume"):
        env["HECATE_E2E_CRASH_AFTER_CONSUME"] = "1"
    env.pop("HECATE_STEP6_POSTGRES_URL", None)
    return env


def _spawn_worker(stack: dict[str, Any]) -> subprocess.Popen:
    log_path = Path(stack["counter_file"]).parent / f"worker_{uuid.uuid4().hex[:8]}.log"
    # The handle's lifetime is the worker process's, not this scope's.
    log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    stack.setdefault("worker_logs", []).append((log_handle, log_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.scenarios.worker_process_e2e"],
        cwd=str(_REPO),
        env=_child_env(stack),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    stack["workers"].append(process)
    return process


async def _poll_task_state(stack: dict[str, Any], task_uuid: uuid.UUID, *states: str, timeout: float = 120.0):
    t_ref = make_task_ref("hecate", str(task_uuid))
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
        last = record
        if record is not None and record.lifecycle_state.value in states:
            return record
        await asyncio.sleep(0.5)
    logs = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")[-5000:] for _, path in stack.get("worker_logs", [])
    )
    raise AssertionError(
        "task never reached "
        f"{states}; last={last.lifecycle_state.value if last else None} "
        f"extra={getattr(last, 'extra', None)}\n{logs}"
    )


async def test_approval_park_survives_process_restart_and_runs_once(restart_stack) -> None:
    stack = restart_stack
    worker_a = _spawn_worker(stack)
    try:
        task_uuid = await _park_via_worker_a(stack, worker_a)
        await _kill_worker(worker_a)
        approval_ref, token = await _approve_and_resume(stack, task_uuid)
        worker_b = _spawn_worker(stack)
        try:
            record = await _poll_task_state(
                stack, task_uuid, "succeeded", "failed", "reconciliation_required", timeout=180.0
            )
            assert record.lifecycle_state.value == "succeeded", record.extra
            await _assert_single_execution(stack, task_uuid, approval_ref)
        finally:
            worker_b.terminate()
            try:
                worker_b.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker_b.kill()
    finally:
        if worker_a.poll() is None:
            worker_a.kill()


@pytest.mark.parametrize("restart_stack", ["precompleted"], indirect=True)
async def test_completed_write_is_replayed_across_approval_restart(restart_stack) -> None:
    """A write before the approval wait remains single-execution after restart."""
    await test_approval_park_survives_process_restart_and_runs_once(restart_stack)


async def test_crash_after_approval_consumption_before_claim_rebinds(restart_stack) -> None:
    """Consumption in a dead process must authorize only its original action."""
    stack = restart_stack
    worker_a = _spawn_worker(stack)
    task_id = await _park_via_worker_a(stack, worker_a)
    await _kill_worker(worker_a)
    approval_ref, _token = await _approve_and_resume(stack, task_id)
    stack["crash_after_consume"] = True
    worker_b = _spawn_worker(stack)
    deadline = asyncio.get_running_loop().time() + 120
    while not Path(stack["consumed_file"]).exists():
        assert asyncio.get_running_loop().time() < deadline, "worker never consumed the binding"
        await asyncio.sleep(0.1)
    assert Path(stack["consumed_file"]).read_text(encoding="utf-8") == approval_ref
    assert not Path(stack["counter_file"]).read_text(encoding="utf-8")
    await _kill_worker(worker_b)
    stack["crash_after_consume"] = False
    _spawn_worker(stack)
    final = await _poll_task_state(stack, task_id, "succeeded", "failed", "reconciliation_required", timeout=120)
    assert final.lifecycle_state.value == "succeeded", final.extra
    await _assert_single_execution(stack, task_id, approval_ref)


@pytest.mark.parametrize("restart_stack", ["repeated"], indirect=True)
async def test_same_arguments_new_action_requires_a_second_decision(restart_stack) -> None:
    """The same tool parameters do not make two logical actions one approval."""
    stack = restart_stack
    worker_a = _spawn_worker(stack)
    task_id = await _park_via_worker_a(stack, worker_a)
    await _kill_worker(worker_a)
    first_ref, _token = await _approve_and_resume(stack, task_id)
    worker_b = _spawn_worker(stack)
    second_wait = await _poll_task_state(stack, task_id, "waiting_approval", "failed", timeout=120)
    assert second_wait.lifecycle_state.value == "waiting_approval", second_wait.extra
    assert len(Path(stack["counter_file"]).read_text(encoding="utf-8").splitlines()) == 1
    second_ref = second_wait.extra["wait"]["contract_ref"]["approval_ref"]
    assert second_ref != first_ref
    await _kill_worker(worker_b)
    approved_ref, _token = await _approve_and_resume(stack, task_id)
    assert approved_ref == second_ref
    _spawn_worker(stack)
    final = await _poll_task_state(stack, task_id, "succeeded", "failed", "reconciliation_required", timeout=120)
    assert final.lifecycle_state.value == "succeeded", final.extra
    executions = [json.loads(line) for line in Path(stack["counter_file"]).read_text(encoding="utf-8").splitlines()]
    assert len(executions) == 2
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with stack["factory"]() as db:
        ledger = SqlApprovalLedger(db)
        first = await ledger.get(first_ref)
        second = await ledger.get(second_ref)
        assert first.consumed_by_invocation_id and second.consumed_by_invocation_id
        assert first.consumed_by_invocation_id != second.consumed_by_invocation_id


async def _park_via_worker_a(stack: dict[str, Any], worker_a: subprocess.Popen) -> uuid.UUID:
    """Submit through the control plane; the child parks on the protected action."""

    async with stack["factory"]() as db:
        result = await _service(stack, db).submit(
            workspace_id=stack["ws_id"],
            user_id=stack["user_id"],
            goal="create the ladder-fix ticket",
            agent_id=stack["agent_id"],
            input={
                "messages": [{"role": "user", "content": "create the ticket"}],
                "approval_wait": True,
            },
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    record = await _poll_task_state(stack, task_uuid, "waiting_approval", "failed", timeout=150.0)
    assert record.lifecycle_state.value == "waiting_approval", record.extra
    wait = (record.extra or {}).get("wait") or {}
    assert wait.get("wake_kind") == "resume"
    assert wait.get("consumed") is False
    contract = wait.get("contract_ref") or {}
    assert contract.get("approval_ref")
    return task_uuid


async def _kill_worker(worker: subprocess.Popen) -> None:
    """Real process death — not a graceful shutdown."""

    worker.kill()
    await asyncio.to_thread(worker.wait, 15)


async def _approve_and_resume(stack: dict[str, Any], task_uuid: uuid.UUID) -> tuple[str, str]:
    """Control-plane facts: durable decision, then the resume command."""

    t_ref = make_task_ref("hecate", str(task_uuid))
    record = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
    wait = (record.extra or {}).get("wait") or {}
    approval_ref = str(wait["contract_ref"]["approval_ref"])
    token = str(wait["wait_token"])

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with stack["factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding is not None
        from hecate_governance.approval import ApprovalJudgment

        assert binding.judgment is ApprovalJudgment.PENDING
        await SqlApprovalLedger(db).decide(approval_ref, approved=True, reason="e2e approval", decided_by="tester")
        await db.commit()
    async with stack["factory"]() as db:
        issued = await _service(stack, db).issue_command(
            workspace_id=stack["ws_id"],
            task_id=task_uuid,
            kind=ControlCommandKind.RESUME,
            issuer="tester",
            detail_ns={"wait_token": token},
            payload={"approval_ref": approval_ref},
            payload_schema_ref="test:approval-resume/0",
        )
    assert issued.record.state is CommandState.APPLIED, issued.detail
    return approval_ref, token


async def _assert_single_execution(stack: dict[str, Any], task_uuid: uuid.UUID, approval_ref: str) -> None:
    """The protected action ran exactly once, in process B; the binding was
    consumed; the resumed run is the SAME attempt; the answer cites the
    real receipt."""

    lines = Path(stack["counter_file"]).read_text(encoding="utf-8").strip().splitlines()
    executions = [json.loads(line) for line in lines if line.strip()]
    assert len(executions) == (2 if stack["mode"] == "precompleted" else 1), executions
    protected = [entry for entry in executions if entry["tool"] == "stub_ticket"]
    assert len(protected) == 1
    assert protected[0]["args"] == {"title": "fix the ladder"}
    if stack["mode"] == "precompleted":
        assert len([entry for entry in executions if entry["tool"] == "stub_prepare"]) == 1

    task = await asyncio.to_thread(stack["store"].get_task_state, make_task_ref("hecate", str(task_uuid)))
    contract = task.extra["wait"]["contract_ref"]

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    async with stack["factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding is not None
        assert binding.consumed_by_invocation_id == contract["invocation_id"]

    async with stack["factory"]() as db:
        from sqlalchemy import select

        from hecate.models.run import RunModel

        runs = (await db.execute(select(RunModel).where(RunModel.task_id == task_uuid))).scalars().all()
        assert len(runs) == 1, "an authorized wake continues the parked attempt, not a new one"
        projection = runs[0].projection or {}
        assert projection.get("state") == "succeeded"
        assert str(runs[0].id) == contract["run_id"]
        assert runs[0].backend_ref["id"] == contract["engine_session_id"]
        assert not projection.get("error_category")

    # Task result carries the tool receipt value into the final answer.
    record = await asyncio.to_thread(stack["store"].get_task_state, make_task_ref("hecate", str(task_uuid)))
    result = (record.extra or {}).get("result") or {}
    content = str(result.get("content") or "")
    assert "TCK-1" in content, content
    trace = [json.loads(line) for line in Path(stack["model_trace_file"]).read_text(encoding="utf-8").splitlines()]
    resumed = [entry for entry in trace if entry["phase"] == "input" and entry["pid"] == protected[0]["pid"]]
    assert resumed
    receipts = [message for message in resumed[0]["messages"] if message.get("role") == "tool"]
    assert any(message.get("content") == "TCK-1: fix the ladder" for message in receipts)
    if stack["mode"] == "precompleted":
        assert any(message.get("content") == "PRE-1: fix the ladder" for message in receipts)

    from hecate_runtime.eventstore import EventType

    from hecate.models.approval import ApprovalAuthorityModel
    from hecate.studio.event_state.postgres_store import PostgresEventStore

    events = await PostgresEventStore(async_session_factory=stack["factory"]).get_events(
        uuid.UUID(contract["engine_session_id"])
    )
    decided = [event for event in events if event.event_type is EventType.APPROVAL_DECIDED]
    assert decided and all(event.payload["approval_ref"] == approval_ref for event in decided)
    async with stack["factory"]() as db:
        from sqlalchemy import select

        authorities = (await db.execute(select(ApprovalAuthorityModel))).scalars().all()
        assert len(authorities) == 1


def _real_model_enabled() -> bool:
    from tests.scenarios.baseline.run_autonomous_baseline import provider_credentials_available

    model = os.environ.get("BASELINE_MODEL", "")
    return os.environ.get("HECATE_E2E_REAL_MODEL") == "1" and bool(model) and provider_credentials_available(model)


@pytest.mark.skipif(
    not _real_model_enabled(),
    reason="HECATE_E2E_REAL_MODEL/BASELINE_MODEL/provider key not configured; real-model durable run reports skip",
)
@pytest.mark.timeout(600)
async def test_durable_approval_restart_with_real_model(restart_stack) -> None:
    """The same park/wake/resume machinery driven by a REAL provider.

    The same exact authority, receipt, original-run and final-result checks
    apply. Actual model inputs/outputs and partial failures are recorded in
    a new artifact; historical observations are never overwritten.
    """

    stack = restart_stack
    # Real model turns are slow: the dispatch lease must outlive them.
    stack["real_model"] = True
    stack["lease_ttl"] = "180"
    started = time.monotonic()
    task_uuid = None
    approval_ref = None
    record_payload = {
        "_meta": {
            "kind": "real_model_durable_approval_restart",
            "evidence_version": 2,
            "status": "failed",
            "gate": False,
            "recorded_at": datetime.now(UTC).isoformat(),
            "model": os.environ.get("BASELINE_MODEL"),
            "source": "real_model",
            "cost_amount": None,
            "price_version": "unverified",
            "historical_record": "durable_approval_restart_20261016T155804310Z.json",
        },
        "parked": False,
        "resumed_after_process_restart": False,
    }
    worker_a = _spawn_worker(stack)
    try:
        async with stack["factory"]() as db:
            result = await _service(stack, db).submit(
                workspace_id=stack["ws_id"],
                user_id=stack["user_id"],
                goal="Create a ticket titled 'fix the ladder' with the create-ticket tool, then report the ticket id.",
                agent_id=stack["agent_id"],
                input={
                    "messages": [
                        {
                            "role": "user",
                            "content": "Create a ticket titled 'fix the ladder' with the create-ticket tool, "
                            "then report the ticket id.",
                        }
                    ],
                    "approval_wait": True,
                },
            )
        task_uuid = uuid.UUID(result.task_ref.id)
        record = await _poll_task_state(stack, task_uuid, "waiting_approval", "failed", timeout=240.0)
        assert record.lifecycle_state.value == "waiting_approval", record.extra
        record_payload["parked"] = True
        await _kill_worker(worker_a)
        approval_ref, _token = await _approve_and_resume(stack, task_uuid)
        worker_b = _spawn_worker(stack)
        record_payload["resumed_after_process_restart"] = True
        try:
            final = await _poll_task_state(
                stack, task_uuid, "succeeded", "failed", "reconciliation_required", timeout=300.0
            )
            assert final.lifecycle_state.value == "succeeded", final.extra
            await _assert_single_execution(stack, task_uuid, approval_ref)
        finally:
            worker_b.terminate()
            try:
                worker_b.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker_b.kill()
        record_payload["_meta"]["status"] = "passed"
    except BaseException as exc:
        record_payload["error_type"] = type(exc).__name__
        raise
    finally:
        if worker_a.poll() is None:
            worker_a.kill()
        try:
            if task_uuid is not None:
                final_state = await asyncio.to_thread(
                    stack["store"].get_task_state, make_task_ref("hecate", str(task_uuid))
                )
                record_payload["task_state"] = final_state.lifecycle_state.value if final_state else None
                if final_state is not None:
                    record_payload["result"] = (final_state.extra or {}).get("result")
                    record_payload["wait_contract"] = ((final_state.extra or {}).get("wait") or {}).get("contract_ref")
            if approval_ref is not None:
                from hecate.enterprise.governance_ledgers import SqlApprovalLedger

                async with stack["factory"]() as db:
                    binding = await SqlApprovalLedger(db).get(approval_ref)
                    record_payload["approval_binding"] = binding.model_dump(mode="json") if binding else None
        except Exception as exc:
            record_payload["observation_error_type"] = type(exc).__name__
        for key, path in (("executions", stack["counter_file"]), ("model_trace", stack["model_trace_file"])):
            record_payload[key] = (
                [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
                if Path(path).exists()
                else []
            )
        record_payload["latency_seconds_total"] = round(time.monotonic() - started, 2)
        out_dir = Path(__file__).resolve().parent / "baselines"
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
        (out_dir / f"durable_approval_restart_{stamp}.json").write_text(
            json.dumps(record_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
