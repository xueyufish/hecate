"""Workflow × durable-approval across a REAL process restart (step5e).

The parent workflow task parks on its declared child; the child parks on a
policy-required approval; the worker process executing both is killed; the
control plane records the approval decision and issues ``resume``; a fresh
worker process resumes the child natively (system-tenant snapshot), the
parent validates the declared artifact on the child's committed facts and
advances to terminal. Only the provider boundary is scripted (same doubles
as the deterministic suite); the side-effect counter is a shared file so
the single-execution claim is observed across process boundaries.

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import CommandState, ControlCommandKind
from hecate.contracts.execution.references import task_ref as make_task_ref

PG_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")

pytestmark = [
    pytest.mark.skipif(
        not PG_URL, reason="HECATE_STEP6_POSTGRES_URL not configured; workflow restart e2e reports skip"
    ),
    pytest.mark.timeout(600),
]

_REPO = Path(__file__).resolve().parents[2]

STEPS = [
    {
        "goal": "Create a ticket titled 'fix the ladder' with the create-ticket tool, then report the ticket id.",
        "approval_wait": True,
        "output_schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        "artifact": "answer",
    },
]


async def _create_database(base_url: str, name: str) -> str:
    admin_url = base_url.replace("+asyncpg", "").replace("+psycopg", "").rsplit("/", 1)[0] + "/postgres"
    import psycopg

    def _create() -> None:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')

    await asyncio.to_thread(_create)
    return base_url.rsplit("/", 1)[0] + "/" + name


@pytest.fixture
async def wf_stack(tmp_path: Path):
    """Unique database + durable store + tenant/agent seeded for workflows."""

    db_name = "e2e_wfa_" + uuid.uuid4().hex[:10]
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
    from hecate.models.tool_policy import ToolPolicyModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel

    org_id = uuid.UUID(int=1)
    ws_id = uuid.UUID(int=2)
    user_id = uuid.UUID(int=3)
    async with factory() as db:
        db.add(OrganizationModel(id=org_id, name="e2e-org", slug="e2e-org", owner_id=user_id))
        db.add(WorkspaceModel(id=ws_id, org_id=org_id, name="e2e-ws", slug="e2e-ws"))
        db.add(UserModel(id=user_id, email="e2e@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=ws_id, name="wf-approval-e2e-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={
                "tools": ["stub_ticket"],
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
        db.add(
            ToolPolicyModel(
                workspace_id=ws_id,
                rule_action="ask",
                tool_pattern="stub_ticket",
                priority=10,
                description="e2e: protected action requires approval",
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
    trace_file = tmp_path / "model_trace.jsonl"
    trace_file.write_text("", encoding="utf-8")
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
        "trace_file": str(trace_file),
        "cleanups": [],
        "lease_ttl": "5",
    }
    try:
        yield state
    finally:
        for process in state.get("processes", []):
            if process.poll() is None:
                await _kill_worker(process)
            else:
                await asyncio.to_thread(process.wait, 15)
        for handle, _path in state.get("worker_logs", []):
            handle.close()
        for cleanup in reversed(state["cleanups"]):
            cleanup()
        await engine.dispose()
        store.dispose()
        admin_url = PG_URL.replace("+psycopg", "").rsplit("/", 1)[0] + "/postgres"
        import psycopg

        def _drop():
            with psycopg.connect(admin_url, autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')

        await asyncio.to_thread(_drop)


class _RequeueWorker:
    """Control plane requeues only; fleet workers execute."""

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
            "HECATE_E2E_MODEL_TRACE_FILE": stack["trace_file"],
            "HECATE_E2E_LEASE_TTL": stack["lease_ttl"],
            "SESSION_STATE_STORE_BACKEND": "postgres",
            "PYTHONPATH": str(_REPO),
        }
    )
    if not stack.get("real_model"):
        env.pop("HECATE_E2E_REAL_MODEL", None)
    env.pop("HECATE_STEP6_POSTGRES_URL", None)
    return env


def _spawn_worker(stack: dict[str, Any], role: str) -> subprocess.Popen:
    log_path = Path(stack["counter_file"]).parent / f"worker_{uuid.uuid4().hex[:8]}.log"
    log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — handle outlives the spawn scope
    stack.setdefault("worker_logs", []).append((log_handle, log_path))
    env = _child_env(stack)
    env["HECATE_E2E_TASK_ROLE"] = role
    pid_file = log_path.with_suffix(".pid")
    env["HECATE_E2E_PID_FILE"] = str(pid_file)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.scenarios.worker_process_e2e"],
        cwd=str(_REPO),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    process.worker_pid_file = pid_file
    stack.setdefault("processes", []).append(process)
    return process


async def _worker_pid(worker: subprocess.Popen) -> int:
    deadline = asyncio.get_running_loop().time() + 120
    while not worker.worker_pid_file.exists():
        assert worker.poll() is None, "worker exited before reporting its PID"
        assert asyncio.get_running_loop().time() < deadline, "worker startup timed out"
        await asyncio.sleep(0.1)
    return int(worker.worker_pid_file.read_text(encoding="utf-8"))


async def _kill_worker(worker: subprocess.Popen) -> None:
    # Windows venv python.exe redirects to a second process. Killing only
    # Popen.pid leaves the real worker alive, invalidating restart evidence.
    if sys.platform == "win32":
        result = await asyncio.to_thread(
            subprocess.run, ["taskkill", "/F", "/T", "/PID", str(worker.pid)], capture_output=True
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    else:
        worker.kill()
    await asyncio.to_thread(worker.wait, 15)


async def _poll_task_state(stack: dict[str, Any], task_uuid: uuid.UUID, *states: str, timeout: float = 240.0):
    t_ref = make_task_ref("hecate", str(task_uuid))
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
        last = record
        if record is not None and record.lifecycle_state.value in states:
            return record
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"task never reached {states}; last={last.lifecycle_state.value if last else None} "
        f"extra={getattr(last, 'extra', None)}"
    )


async def _find_parked_child(stack: dict[str, Any], parent_uuid: uuid.UUID) -> tuple[uuid.UUID, dict[str, Any]]:
    t_ref = make_task_ref("hecate", str(parent_uuid))
    payload = await asyncio.to_thread(stack["store"].get_task_input, t_ref) or {}
    declared = payload.get("workflow", {}).get("steps", [])
    approval_steps = {i for i, step in enumerate(declared) if step.get("approval_wait")}

    async with stack["factory"]() as db:
        from sqlalchemy import select

        from hecate.models.task import TaskModel

        candidates = (
            (await db.execute(select(TaskModel).where(TaskModel.workspace_id == stack["ws_id"]))).scalars().all()
        )
    for task in candidates:
        if str(task.id) == str(parent_uuid):
            continue
        child_ref = make_task_ref("hecate", str(task.id))
        child_payload = await asyncio.to_thread(stack["store"].get_task_input, child_ref) or {}
        stamp = child_payload.get("workflow_parent") or {}
        if stamp.get("task_ref", {}).get("id") != str(parent_uuid):
            continue
        if stamp.get("step_index") not in approval_steps:
            continue
        record = await _poll_task_state(stack, task.id, "waiting_approval", timeout=240.0)
        return task.id, (record.extra or {}).get("wait") or {}
    raise AssertionError(f"no parked approval child found for parent {parent_uuid}")


async def _approve_and_resume(stack: dict[str, Any], child_id: uuid.UUID, wait: dict[str, Any]) -> None:
    """Control-plane facts: durable decision, then the resume command."""

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger

    approval_ref = str(wait["contract_ref"]["approval_ref"])
    async with stack["factory"]() as db:
        await SqlApprovalLedger(db).decide(approval_ref, approved=True, reason="e2e approval", decided_by="tester")
        await db.commit()
    async with stack["factory"]() as db:
        issued = await _service(stack, db).issue_command(
            workspace_id=stack["ws_id"],
            task_id=child_id,
            kind=ControlCommandKind.RESUME,
            issuer="tester",
            detail_ns={"wait_token": str(wait["wait_token"])},
            payload={"approval_ref": approval_ref},
            payload_schema_ref="test:approval-resume/0",
        )
    assert issued.record.state is CommandState.APPLIED, issued.detail


async def _run_restart(stack: dict[str, Any], window: str) -> None:
    """Run production parent/child workers in distinct OS processes."""
    from sqlalchemy import select

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from hecate.models.run import RunModel

    parent_worker = _spawn_worker(stack, "parent")
    child_worker = _spawn_worker(stack, "child")
    original_parent_pid = await _worker_pid(parent_worker)
    original_child_pid = await _worker_pid(child_worker)
    async with stack["factory"]() as db:
        result = await _service(stack, db).submit(
            workspace_id=stack["ws_id"],
            user_id=stack["user_id"],
            goal="run the approval workflow",
            agent_id=stack["agent_id"],
            input={"messages": [{"role": "user", "content": "run"}], "workflow": {"steps": STEPS}},
        )
    parent_id = uuid.UUID(result.task_ref.id)
    stack["parent_id"] = parent_id
    parent = await _poll_task_state(stack, parent_id, "waiting_input", "failed")
    assert parent.lifecycle_state.value == "waiting_input", parent.extra
    original_parent_wait = parent.extra["wait"]
    child_id, child_wait = await _find_parked_child(stack, parent_id)
    stack.update(child_id=child_id, child_wait=child_wait, parked=True)
    assert child_wait["consumed"] is False
    assert not _read_jsonl(stack["counter_file"])
    assert original_parent_wait["contract_ref"]["await_task_ref"]["id"] == str(child_id)
    approval_ref = child_wait["contract_ref"]["approval_ref"]
    async with stack["factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.judgment.value == "pending"
        original_run = (await db.execute(select(RunModel).where(RunModel.task_id == child_id))).scalar_one()
        original_run_id, original_session = original_run.id, dict(original_run.backend_ref)

    if window == "both_separately":
        await _kill_worker(parent_worker)
        parent_worker = _spawn_worker(stack, "parent")
        assert await _worker_pid(parent_worker) != original_parent_pid
    await _kill_worker(child_worker)
    # Restarting a waiting parent cannot consume its child's pending wait.
    parent = await _poll_task_state(stack, parent_id, "waiting_input")
    assert parent.extra["wait"] == original_parent_wait
    await _approve_and_resume(stack, child_id, child_wait)
    resumed_worker = _spawn_worker(stack, "child")
    resumed_pid = await _worker_pid(resumed_worker)
    stack["resumed_pid"] = resumed_pid
    assert resumed_pid != original_child_pid
    stack["resumed_after_process_restart"] = True
    final = await _poll_task_state(stack, parent_id, "succeeded", "failed", "reconciliation_required", timeout=600)
    assert final.lifecycle_state.value == "succeeded", final.extra
    assert final.extra["result"]["status"] == "succeeded"
    child = await _poll_task_state(stack, child_id, "succeeded", "failed", "reconciliation_required", timeout=30)
    assert child.lifecycle_state.value == "succeeded", child.extra
    child_result = child.extra["result"]
    document = json.loads(child_result["content"])
    assert isinstance(document["answer"], str) and document["answer"], child_result
    assert "TCK-1" in child_result["content"], child_result
    assert child_result["artifacts"]["answer"] == document["answer"]
    assert set(final.extra["_workflow_callbacks"]) == {"0"}
    assert final.extra["_workflow_callbacks"]["0"]["child_task_id"] == str(child_id)

    executions = _read_jsonl(stack["counter_file"])
    assert len(executions) == 1, executions
    assert executions[0]["tool"] == "stub_ticket"
    assert executions[0]["pid"] == resumed_pid
    async with stack["factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.consumed_by_invocation_id == child_wait["contract_ref"]["invocation_id"]
        runs = (await db.execute(select(RunModel).where(RunModel.task_id == child_id))).scalars().all()
        assert len(runs) == 1 and runs[0].id == original_run_id
        assert runs[0].backend_ref == original_session
        from hecate.models.approval import ApprovalAuthorityModel

        authorities = (await db.execute(select(ApprovalAuthorityModel))).scalars().all()
        assert len(authorities) == 1
    from hecate_runtime.eventstore import EventType

    from hecate.studio.event_state.postgres_store import PostgresEventStore

    events = await PostgresEventStore(async_session_factory=stack["factory"]).get_events(
        uuid.UUID(original_session["id"])
    )
    decisions = [event for event in events if event.event_type is EventType.APPROVAL_DECIDED]
    assert decisions and all(event.payload["approval_ref"] == approval_ref for event in decisions)
    # The first new inference must see the real receipt from the resumed
    # action, with its actual content rather than an error placeholder.
    inputs = [row for row in _read_jsonl(stack["trace_file"]) if row["phase"] == "input" and row["pid"] == resumed_pid]
    assert inputs, "resumed worker did not continue inference"
    receipt = f"TCK-1: {executions[0]['args']['title']}"
    assert any(m.get("role") == "tool" and receipt in str(m.get("content")) for m in inputs[0]["messages"])


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    file = Path(path)
    return (
        [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if file.exists()
        else []
    )


@pytest.mark.parametrize("window", ["child_only", "both_separately"])
async def test_workflow_child_approval_survives_process_restart(wf_stack, window) -> None:
    """Exercise child-only and separate parent/child process restarts."""
    await _run_restart(wf_stack, window)


def _real_model_enabled() -> bool:
    from tests.scenarios.baseline.run_autonomous_baseline import provider_credentials_available

    model = os.environ.get("BASELINE_MODEL", "")
    return os.environ.get("HECATE_E2E_REAL_MODEL") == "1" and bool(model) and provider_credentials_available(model)


@pytest.mark.skipif(not _real_model_enabled(), reason="real-model opt-in/model/matching provider key not configured")
@pytest.mark.timeout(900)
async def test_workflow_child_approval_restart_with_real_model(wf_stack) -> None:
    """Keep successful and failed real-provider observations with raw facts."""
    stack = wf_stack
    stack.update(real_model=True, lease_ttl="180")
    started = time.monotonic()
    record = {
        "_meta": {
            "kind": "real_model_workflow_approval_restart",
            "evidence_version": 2,
            "status": "failed",
            "gate": False,
            "recorded_at": datetime.now(UTC).isoformat(),
            "model": os.environ.get("BASELINE_MODEL"),
            "source": "real_model",
            "cost_amount": None,
            "price_version": "unverified",
            "historical_record": "workflow_approval_restart_20261017T052629590Z.json",
        }
    }
    try:
        await _run_restart(stack, "both_separately")
        record["_meta"]["status"] = "passed"
    except BaseException as exc:
        record["error_type"] = type(exc).__name__
        raise
    finally:
        for process in stack.get("processes", []):
            if process.poll() is None:
                await _kill_worker(process)
        record.update(
            parked=stack.get("parked", False),
            resumed_after_process_restart=stack.get("resumed_after_process_restart", False),
            latency_seconds_total=round(time.monotonic() - started, 2),
            executions=_read_jsonl(stack["counter_file"]),
            model_trace=_read_jsonl(stack["trace_file"]),
            processes=[
                {
                    "launcher_pid": process.pid,
                    "worker_pid": int(process.worker_pid_file.read_text(encoding="utf-8"))
                    if process.worker_pid_file.exists()
                    else None,
                    "exit_code": process.poll(),
                }
                for process in stack.get("processes", [])
            ],
        )
        try:
            for name in ("parent", "child"):
                if stack.get(f"{name}_id") is not None:
                    state = await asyncio.to_thread(
                        stack["store"].get_task_state, make_task_ref("hecate", str(stack[f"{name}_id"]))
                    )
                    record[name] = {"state": state.lifecycle_state.value, "extra": state.extra} if state else None
            if stack.get("child_wait"):
                from hecate.enterprise.governance_ledgers import SqlApprovalLedger

                async with stack["factory"]() as db:
                    binding = await SqlApprovalLedger(db).get(stack["child_wait"]["contract_ref"]["approval_ref"])
                    record["approval_binding"] = binding.model_dump(mode="json") if binding else None
        except Exception as exc:
            record["observation_error_type"] = type(exc).__name__
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
        (Path(__file__).parent / "baselines" / f"workflow_approval_restart_{stamp}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
