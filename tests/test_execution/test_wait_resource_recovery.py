"""Managed wait resource release and restart wakeup acceptance.

Platform-local controller tasks, pure API tools, persistent SQL state —
the dispatch, persistence, commands and wakes are the production chain;
only the model and business tool are deterministic doubles.

Hard assertions (resource release and execution correctness):

- After a park, the dispatch call has returned: the dispatch lease is
  re-acquirable by another process, the waiting task makes no new model
  asks or business calls across observed worker cycles, and the same
  worker still drives another runnable task to terminal.
- After a confirmed park, killing the worker process and starting a fresh
  one recovers from persistent state only: committed decisions are not
  re-asked, completed actions are not re-executed, and a redelivered wake
  command consumes exactly once (a distinct late command is rejected).
- Cancel during a wait lands the terminal; a late wake cannot resurrect
  it. Timer expiry converges once; late scans are no-ops.

All three wait kinds run through real worker processes. SQLite is the
local tier; HECATE_STEP6_POSTGRES_URL selects an isolated PostgreSQL schema.
Completed worker cycles expose inflight dispatches, heartbeat tasks,
connection checkouts and process CPU time. Parent monotonic timestamps
measure recovery and wake latency. Each test writes a separate JSON file
under HECATE_WAITRES_EVIDENCE_DIR, including the source digests and database
tier; the PostgreSQL CI step retains these files as an artifact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hecate_durable.storage.lease import task_dispatch_lease_key

from hecate.contracts.execution.durable import ControlCommandKind
from hecate.contracts.execution.references import task_ref as make_task_ref
from tests.test_execution.test_controller_loop_dispatch import (
    ORG,
    OWNER,
    WS,
    _ProductionInputPort,
    _ScriptedAssembly,
)
from tests.test_execution.test_controller_loop_dispatch import _no_guardrails as _neutral_guardrails
from tests.test_execution.test_timer_wait import readonly_poll_tool  # noqa: F401 - fixture registration

pytestmark = pytest.mark.timeout(420)

_REPO = Path(__file__).resolve().parents[2]


class _RequeueWorker:
    """Control-plane stand-in: requeue only, fleet workers own every claim."""

    async def dispatch_once(self, task_ref) -> bool:
        return False


@pytest.fixture
async def stack(tmp_path: Path, monkeypatch):
    """One isolated SQL stack shared with the worker subprocesses."""

    from types import SimpleNamespace

    from hecate_durable.storage import SqlDurableStore
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from hecate.core.database import Base
    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel
    from hecate.models.organization import OrganizationModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel
    from tests.test_execution.conftest import _harness_schema_name, _pg_url_with_schema

    postgres_url = os.environ.get("HECATE_STEP6_POSTGRES_URL")
    schema = None
    if postgres_url:
        schema = _harness_schema_name(tmp_path)
        database_url = postgres_url.replace("+psycopg", "+asyncpg")
        admin = create_async_engine(database_url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        await admin.dispose()
        async_engine = create_async_engine(database_url, connect_args={"server_settings": {"search_path": schema}})
        store = SqlDurableStore(_pg_url_with_schema(postgres_url, schema), source="platform")
    else:
        import sqlite3

        db_file = tmp_path / "wait-resource.db"
        with sqlite3.connect(db_file) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        database_url = f"sqlite+aiosqlite:///{db_file}"
        async_engine = create_async_engine(database_url, connect_args={"timeout": 30})
        store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    await asyncio.to_thread(store.create_schema)
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    org_id, ws_id, user_id = ORG, WS, OWNER
    async with session_factory() as db:
        db.add(OrganizationModel(id=org_id, name="wr-org", slug="wr-org", owner_id=user_id))
        db.add(WorkspaceModel(id=ws_id, org_id=org_id, name="wr-ws", slug="wr-ws"))
        db.add(UserModel(id=user_id, email="wr@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=ws_id, name="wait-resource-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"tools": ["query_inventory"], "model_config": {"model": "stub-model"}},
            content_hash="a" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id, agent_id=agent.id, workspace_id=ws_id, organization_id=org_id, owner_user_id=user_id
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

    # Fleet-worker mode for the whole drill: the control plane requeues
    # only; the real claims happen in the worker processes under test.
    import hecate.execution.task_control as task_control_module

    saved_spawn = task_control_module._spawn_background
    task_control_module._spawn_background = lambda coroutine: coroutine.close()

    model_trace = tmp_path / "model_trace.jsonl"
    tool_trace = tmp_path / "tool_trace.jsonl"
    state = {
        "store": store,
        "suite": SimpleNamespace(store=store, recorder=store),
        "cleanups": [],
        "session_factory": session_factory,
        "agent_id": agent_id,
        "ws_id": ws_id,
        "dsn": store.engine.url.render_as_string(hide_password=False),
        "durable_dsn": store.engine.url.render_as_string(hide_password=False),
        "database_url": database_url,
        "schema": schema,
        "database": "postgresql" if postgres_url else "sqlite",
        "tmp_path": tmp_path,
        "model_trace": str(model_trace),
        "tool_trace": str(tool_trace),
        "workers": [],
        "worker_logs": [],
        "started_at": datetime.now(UTC).isoformat(),
        "code_version": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO, text=True).strip(),
        "code_dirty": subprocess.call(["git", "diff", "--quiet", "HEAD"], cwd=_REPO) != 0,
        "source_sha256": {
            name: hashlib.sha256((_REPO / "tests/test_execution" / name).read_bytes()).hexdigest()
            for name in ("test_wait_resource_recovery.py", "_wait_resource_process.py")
        },
    }
    try:
        yield state
    finally:
        task_control_module._spawn_background = saved_spawn
        for process in state["workers"]:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 15)
        for handle, _path in state["worker_logs"]:
            handle.close()
        for cleanup in reversed(state["cleanups"]):
            cleanup()
        await async_engine.dispose()
        store.dispose()
        if schema:
            admin = create_async_engine(database_url)
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()


def _service(stack: dict[str, Any], db):
    from hecate.execution.task_control import TaskControlService

    return TaskControlService(
        db,
        store=stack["store"],
        recorder=stack["store"],
        backend="postgres",
        ledger_source="core",
        session_factory=stack["session_factory"],
        worker=_RequeueWorker(),
    )


async def _submit(stack: dict[str, Any], mode: str, **overrides: Any) -> tuple[Any, _WaitPort]:
    """Submit one controller task through the production control plane."""

    port = _WaitPort(mode)
    assembly = _ScriptedAssembly(port, guardrails=_neutral_guardrails)
    assembly.patch()
    stack.setdefault("cleanups", []).append(assembly.unpatch)
    async with stack["session_factory"]() as db:
        result = await _service(stack, db).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal=f"wait-resource {mode}",
            agent_id=stack["agent_id"],
            input={
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 6, **({"timer_wait": {}} if mode == "timer" else {})},
                **({"approval_wait": True} if mode == "approval" else {}),
                **overrides,
            },
        )
    return result, port


class _WaitPort(_ProductionInputPort):
    """The in-process twin of the child's scripted port (input mode).

    A task whose first user message contains ``direct`` completes without
    the input request — the "same worker serves another task" probe.
    """

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        if mode not in {"input", "approval", "timer"}:  # pragma: no cover - guarded by the caller
            raise ValueError(f"in-process park coverage has no {mode} mode")

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        self.messages = messages
        first_user = next((message.get("content") for message in messages if message.get("role") == "user"), "")
        direct = isinstance(first_user, str) and "direct" in first_user
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "query_inventory",
                            "arguments": json.dumps({"sku": "S-9", **({"region": "EU"} if direct else {})}),
                        },
                    }
                ],
            }
        elif direct:
            yield {"content": json.dumps({"answer": "direct done"}), "tool_calls": None}


async def _wait_state(stack: dict[str, Any], task_uuid: uuid.UUID, *states: str, timeout: float = 60.0):
    t_ref = make_task_ref("hecate", str(task_uuid))
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        last = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
        if last is not None and last.lifecycle_state.value in states:
            return last
        if last is not None and last.lifecycle_state.value in {
            "succeeded",
            "failed",
            "cancelled",
            "reconciliation_required",
        }:
            raise AssertionError(f"expected {states}, got {last.lifecycle_state.value}: {last.extra}")
        await asyncio.sleep(0.1)
    extra = getattr(last, "extra", None)
    raise AssertionError(
        f"task never reached {states}; last={getattr(last, 'lifecycle_state', None)} extra={extra} "
        f"logs={[(path.name, path.read_text(encoding='utf-8', errors='replace')[-1500:]) for _, path in stack['worker_logs']]}"  # noqa: E501
    )


def _child_env(stack: dict[str, Any], mode: str, role: str) -> dict[str, str]:
    from tests.test_execution._agent_child_db import child_environment

    env = child_environment(stack)
    ready = Path(stack["tmp_path"]) / f"ready-{role}.txt"
    pid = Path(stack["tmp_path"]) / f"pid-{role}.txt"
    ready.unlink(missing_ok=True)
    pid.unlink(missing_ok=True)
    env.update(
        {
            "DATABASE_URL": stack["database_url"],
            "HECATE_WAITRES_DSN": stack["dsn"],
            "HECATE_WAITRES_DATABASE_URL": stack["database_url"],
            "HECATE_WAITRES_MODE": mode,
            "HECATE_WAITRES_TOOL_TRACE": stack["tool_trace"],
            "HECATE_WAITRES_MODEL_TRACE": stack["model_trace"],
            "HECATE_WAITRES_READY_FILE": str(ready),
            "HECATE_WAITRES_PID_FILE": str(pid),
            "HECATE_WAITRES_CYCLE_TRACE": str(Path(stack["tmp_path"]) / f"cycles-{role}.jsonl"),
            "HECATE_WAITRES_LEASE_TTL": "30",
            "HECATE_WAITRES_CODE_VERSION": stack["code_version"],
            "PYTHONPATH": str(_REPO),
        }
    )
    return env


def _spawn_worker(stack: dict[str, Any], mode: str, role: str, timeout: float = 30.0) -> subprocess.Popen:
    log_path = Path(stack["tmp_path"]) / f"worker-{role}.log"
    log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - lives with the process
    stack["worker_logs"].append((log_handle, log_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.test_execution._wait_resource_process"],
        cwd=str(_REPO),
        env=_child_env(stack, mode, role),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    stack["workers"].append(process)
    ready = Path(stack["tmp_path"]) / f"ready-{role}.txt"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"worker {role} exited during startup: {log_path.read_text(encoding='utf-8')[-2000:]}")
        if ready.is_file():
            stack.setdefault("pids", {})[role] = int(ready.read_text(encoding="utf-8"))
            stack["role"] = role
            return process
        time.sleep(0.1)
    process.kill()
    raise AssertionError(f"worker {role} never signalled ready: {log_path.read_text(encoding='utf-8')[-2000:]}")


def _trace_lines(path: str) -> list[dict]:
    location = Path(path)
    if not location.is_file():
        return []
    content = location.read_text(encoding="utf-8")
    return [json.loads(line) for line in content.split("\n")[:-1] if line.strip()]


async def _cycles(stack: dict[str, Any], cycles: int = 3) -> list[dict]:
    """Observe completed production cycles and their released resources."""
    role = stack["role"]
    trace = str(Path(stack["tmp_path"]) / f"cycles-{role}.jsonl")
    before = _trace_lines(trace)
    first = before[-1]["cycle"] if before else 0
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert stack["workers"][-1].poll() is None, "worker exited during observation"
        samples = [entry for entry in _trace_lines(trace) if entry["cycle"] > first]
        if len(samples) >= cycles:
            assert all(entry["inflight"] == entry["heartbeats"] == entry["connections"] == 0 for entry in samples)
            return samples
        await asyncio.sleep(0.1)
    raise AssertionError(f"worker {role} did not complete {cycles} cycles")


async def _write_evidence(stack: dict[str, Any], payload: dict) -> Path:
    if "run_ref" not in payload:
        task_ref = make_task_ref("hecate", payload["task_ref"]["id"])
        payload["run_ref"] = (await asyncio.to_thread(stack["store"].run_for_task, task_ref)).to_dict()
    if "evidence_path" not in stack:
        directory = Path(os.environ.get("HECATE_WAITRES_EVIDENCE_DIR", str(stack["tmp_path"])))
        stack["evidence_path"] = directory / f"{Path(stack['tmp_path']).name}-{uuid.uuid4().hex[:8]}.json"
    path = stack["evidence_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    existing.setdefault("database", stack["database"])
    existing.setdefault("code_version", stack["code_version"])
    existing.setdefault("code_dirty", stack["code_dirty"])
    existing.setdefault("source_sha256", stack["source_sha256"])
    existing.setdefault("started_at", stack["started_at"])
    existing.setdefault("observations", []).append(payload)
    path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wait-resource evidence: {path}")
    return path


@pytest.mark.parametrize("mode", ["input", "approval", "timer"])
async def test_park_releases_resources_and_the_worker_keeps_serving(stack, mode) -> None:
    """A parked task holds no dispatch resources; the worker moves on."""
    _spawn_worker(stack, mode, "A")
    result, _port = await _submit(stack, mode)
    task_uuid = uuid.UUID(result.task_ref.id)
    waiting = "waiting_approval" if mode == "approval" else "waiting_input"
    record = await _wait_state(stack, task_uuid, waiting)
    await _cycles(stack, cycles=1)
    dispatch_starts = record.extra.get("dispatch_starts", 0)
    assert dispatch_starts >= 1
    parked_at = datetime.now(UTC)

    # The dispatch call has returned: the dispatch lease is free for
    # another process to claim (acquire + release immediately).
    t_ref = make_task_ref("hecate", str(task_uuid))
    handle = await asyncio.to_thread(
        stack["store"].leases.acquire,
        task_dispatch_lease_key(t_ref.issuer_domain, t_ref.id),
        "probe-reader",
        5,
    )
    assert handle is not None, "dispatch lease still held after the park"
    await asyncio.to_thread(
        stack["store"].leases.release, handle.lease_key, handle.holder, fencing_token=handle.fencing_token
    )

    # The observation window: full worker cycles pass with zero new model
    # asks or business calls for the waiting task.
    model_before = len(_trace_lines(stack["model_trace"]))
    tool_before = len(_trace_lines(stack["tool_trace"]))
    samples = await _cycles(stack, cycles=3)
    record = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
    assert record.lifecycle_state.value == waiting
    assert len(_trace_lines(stack["model_trace"])) == model_before
    assert len(_trace_lines(stack["tool_trace"])) == tool_before
    model_idle = len(_trace_lines(stack["model_trace"]))
    tool_idle = len(_trace_lines(stack["tool_trace"]))

    # The same worker still drives another runnable task to terminal.
    other, _port_b = await _submit(stack, mode, messages=[{"role": "user", "content": "direct"}])
    other_uuid = uuid.UUID(other.task_ref.id)
    await _wait_state(stack, other_uuid, "succeeded")
    after_other = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
    assert after_other.lifecycle_state.value == waiting and after_other.extra["dispatch_starts"] == dispatch_starts
    model_after = len(_trace_lines(stack["model_trace"]))
    tool_after = len(_trace_lines(stack["tool_trace"]))
    assert model_after > model_before and tool_after == tool_before

    await _write_evidence(
        stack,
        {
            "wait_type": mode,
            "task_ref": {"id": str(task_uuid), "issuer": "hecate"},
            "run_ref": (await asyncio.to_thread(stack["store"].run_for_task, t_ref)).to_dict(),
            "worker_pid": stack["pids"]["A"],
            "dispatch_starts": dispatch_starts,
            "lease_probe": "reacquirable-after-park",
            "observation": {
                "from": parked_at.isoformat(),
                "model_asks_before": model_before,
                "model_asks_after": model_idle,
                "tool_calls_before": tool_before,
                "tool_calls_after": tool_idle,
                "cycles": samples,
                "cpu_seconds": samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"],
                "wall_seconds": samples[-1]["monotonic_seconds"] - samples[0]["monotonic_seconds"],
            },
            "other_task": {"id": str(other_uuid), "terminal": "succeeded"},
        },
    )


async def _wake(stack, task_uuid, wait, mode, command_id):
    kind = ControlCommandKind.RESUME if mode == "approval" else ControlCommandKind.PROVIDE_INPUT
    payload = {"approval_ref": wait["contract_ref"]["approval_ref"]} if mode == "approval" else {"region": "EU"}
    async with stack["session_factory"]() as db:
        return await _service(stack, db).issue_command(
            workspace_id=WS,
            task_id=task_uuid,
            kind=kind,
            issuer="editor",
            payload=payload,
            payload_schema_ref="test:approval-resume/0" if mode == "approval" else "urn:hecate:task-user-input/0",
            command_id=command_id,
            expected_revision=None,
            detail_ns={"wait_token": wait["wait_token"]},
        )


@pytest.mark.parametrize("mode", ["input", "approval", "timer"])
async def test_restart_wakes_once_without_replay_or_resurrection(stack, mode) -> None:
    """A fresh process restores each wait and consumes its wake exactly once."""
    worker_a = _spawn_worker(stack, mode, "A")
    controller = {"max_turns": 3, "deadline": (datetime.now(UTC) + timedelta(minutes=30)).isoformat()}
    if mode == "timer":
        controller["timer_wait"] = {}
    result, _port = await _submit(stack, mode, controller=controller)
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = make_task_ref("hecate", str(task_uuid))
    parked = await _wait_state(stack, task_uuid, "waiting_approval" if mode == "approval" else "waiting_input")
    await _cycles(stack, cycles=1)
    wait = parked.extra["wait"]
    run_before = await asyncio.to_thread(stack["store"].run_for_task, t_ref)
    actions_before = await asyncio.to_thread(stack["store"].list_run_actions, run_before)
    model_at_park = len(_trace_lines(stack["model_trace"]))
    tool_at_park = len(_trace_lines(stack["tool_trace"]))
    recovery_started = time.monotonic()
    worker_a.kill()
    await asyncio.to_thread(worker_a.wait, 15)
    _spawn_worker(stack, mode, "B")
    samples = await _cycles(stack, cycles=2)
    recovery_latency = time.monotonic() - recovery_started
    assert len(_trace_lines(stack["model_trace"])) == model_at_park
    assert len(_trace_lines(stack["tool_trace"])) == tool_at_park
    assert (await asyncio.to_thread(stack["store"].get_task_state, t_ref)).extra["wait"] == wait

    wake_started = time.monotonic()
    if mode == "timer":
        from tests.test_execution.test_timer_wait import _timer_waker

        due = datetime.fromisoformat(wait["wait_expires_at"]) + timedelta(seconds=1)
        waker = _timer_waker(stack, clock=lambda: due)
        assert await waker.wake_once() == 1
        assert await waker.wake_once() == 0
        wake_evidence = {"first_scan": 1, "duplicate_scan": 0, "clock": "injected due time"}
    else:
        if mode == "approval":
            from hecate.enterprise.governance_ledgers import SqlApprovalLedger

            async with stack["session_factory"]() as db:
                await SqlApprovalLedger(db).decide(
                    wait["contract_ref"]["approval_ref"],
                    approved=True,
                    reason="wait-resource acceptance",
                    decided_by=str(uuid.uuid4()),
                )
                await db.commit()
        command_id = f"wr-{task_uuid}"
        first = await _wake(stack, task_uuid, wait, mode, command_id)
        assert first.record.state.value == "applied", first.detail
        wake_evidence = {"first": first.record.state.value}
    record = await _wait_state(stack, task_uuid, "succeeded")
    wake_latency = time.monotonic() - wake_started
    await _cycles(stack, cycles=1)
    if mode != "timer":
        replay = await _wake(stack, task_uuid, wait, mode, command_id)
        late = await _wake(stack, task_uuid, wait, mode, f"{command_id}-late")
        assert replay.record.state.value == "applied" and "replay" in (replay.detail or "")
        assert late.record.state.value == "rejected", late.detail
        wake_evidence.update(redelivery=replay.record.state.value, late_distinct=late.record.state.value)
    assert len(_trace_lines(stack["tool_trace"])) == tool_at_park + 1
    assert len(_trace_lines(stack["model_trace"])) == model_at_park + (1 if mode == "approval" else 2)
    assert await asyncio.to_thread(stack["store"].run_for_task, t_ref) == run_before
    payload_after = await asyncio.to_thread(stack["store"].get_task_input, t_ref)
    assert payload_after["controller"] == controller
    assert record.extra["wait"]["contract_ref"] == wait["contract_ref"]
    assert record.extra["wait"]["wait_expires_at"] == wait["wait_expires_at"]
    revisions = await asyncio.to_thread(stack["store"].list_input_revisions, t_ref)
    assert len(revisions) == (1 if mode == "input" else 0)
    actions_after = await asyncio.to_thread(stack["store"].list_run_actions, run_before)
    if mode != "approval":
        assert actions_after[0] == actions_before[0]
    async with stack["session_factory"]() as db:
        cancel = await _service(stack, db).issue_command(
            workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="editor"
        )
    assert cancel.record.state.value == "rejected"
    await _write_evidence(
        stack,
        {
            "wait_type": mode,
            "task_ref": t_ref.to_dict(),
            "run_ref": run_before.to_dict(),
            "processes": {"killed_pid": stack["pids"]["A"], "recovery_pid": stack["pids"]["B"]},
            "recovery_latency_seconds": recovery_latency,
            "wake_to_terminal_seconds": wake_latency,
            "latency_source": "parent monotonic clock",
            "recovery_cycles": samples,
            "wake": wake_evidence,
            "model_asks_total": len(_trace_lines(stack["model_trace"])),
            "business_calls_total": len(_trace_lines(stack["tool_trace"])),
            "terminal": record.lifecycle_state.value,
        },
    )


@pytest.mark.parametrize("disposition", ["cancel", "expiry", "revocation"])
async def test_restart_rechecks_wait_disposition_before_business_work(stack, disposition) -> None:
    """Cancellation, expiry and revoked approval survive a worker replacement."""
    mode = "approval" if disposition == "revocation" else "input"
    worker_a = _spawn_worker(stack, mode, "A")
    result, _port = await _submit(stack, mode, controller={"max_turns": 3, "input_wait_timeout_seconds": 600})
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = make_task_ref("hecate", str(task_uuid))
    parked = await _wait_state(stack, task_uuid, "waiting_approval" if mode == "approval" else "waiting_input")
    await _cycles(stack, cycles=1)
    wait = parked.extra["wait"]
    counts = (len(_trace_lines(stack["model_trace"])), len(_trace_lines(stack["tool_trace"])))
    worker_a.kill()
    await asyncio.to_thread(worker_a.wait, 15)
    if disposition == "cancel":
        async with stack["session_factory"]() as db:
            receipt = await _service(stack, db).issue_command(
                workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="editor"
            )
        assert receipt.record.state.value == "applied"
    elif disposition == "expiry":
        from hecate.execution.input_wait_expiration import InputWaitExpirer

        due = datetime.fromisoformat(wait["wait_expires_at"]) + timedelta(seconds=1)
        assert await InputWaitExpirer(stack["store"], stack["session_factory"], clock=lambda: due).expire_once() == 1
    else:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger

        async with stack["session_factory"]() as db:
            ledger = SqlApprovalLedger(db)
            reference = wait["contract_ref"]["approval_ref"]
            await ledger.decide(reference, approved=True, reason="then revoked", decided_by=str(uuid.uuid4()))
            await ledger.revoke(reference)
            await db.commit()
    _spawn_worker(stack, mode, "B")
    samples = await _cycles(stack, cycles=2)
    rejected = await _wake(stack, task_uuid, wait, mode, f"late-{task_uuid}")
    assert rejected.record.state.value == "rejected", rejected.detail
    fresh = await asyncio.to_thread(stack["store"].get_task_state, t_ref)
    expected = {"cancel": "cancelled", "expiry": "failed", "revocation": "waiting_approval"}[disposition]
    assert fresh.lifecycle_state.value == expected
    assert counts == (len(_trace_lines(stack["model_trace"])), len(_trace_lines(stack["tool_trace"])))
    await _write_evidence(
        stack,
        {
            "disposition": disposition,
            "wait_type": mode,
            "task_ref": t_ref.to_dict(),
            "terminal_or_wait": expected,
            "recovery_cycles": samples,
            "late_wake": "rejected",
        },
    )


async def test_restart_keeps_the_committed_turns_inside_the_original_cap(stack) -> None:
    """The pre-park turn still counts against the limit after process replacement."""
    worker_a = _spawn_worker(stack, "input", "A")
    result, _port = await _submit(stack, "input", controller={"max_turns": 2})
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = make_task_ref("hecate", str(task_uuid))
    parked = await _wait_state(stack, task_uuid, "waiting_input")
    await _cycles(stack, cycles=1)
    worker_a.kill()
    await asyncio.to_thread(worker_a.wait, 15)
    _spawn_worker(stack, "input", "B")
    await _cycles(stack, cycles=1)
    receipt = await _wake(stack, task_uuid, parked.extra["wait"], "input", f"cap-{task_uuid}")
    assert receipt.record.state.value == "applied"
    record = await _wait_state(stack, task_uuid, "failed")
    assert "turn" in record.extra["result"]["error_category"]
    assert len(_trace_lines(stack["model_trace"])) == 2
    assert len(_trace_lines(stack["tool_trace"])) == 2
    await _write_evidence(
        stack,
        {
            "wait_type": "input",
            "task_ref": t_ref.to_dict(),
            "turn_cap": 2,
            "model_asks_total": 2,
            "terminal": "failed",
            "reason": record.extra["result"]["error_category"],
        },
    )


async def test_timer_wait_holds_no_resources_and_each_scan_wakes_once(stack, readonly_poll_tool) -> None:  # noqa: F811 - fixture registration
    """A parked timer wait holds no dispatch resources; scans wake once."""
    from tests.test_execution.test_timer_wait import (
        _dispatch_to_state,
        _parked_timer_task,
        _timer_waker,
        _TimerPollPort,
    )

    task_uuid, t_ref, _wait, worker = await _parked_timer_task(stack, _TimerPollPort())
    parked_at = datetime.now(UTC)

    handle = await asyncio.to_thread(
        stack["store"].leases.acquire,
        task_dispatch_lease_key(t_ref.issuer_domain, t_ref.id),
        "probe-reader",
        5,
    )
    assert handle is not None, "dispatch lease still held after the timer park"
    await asyncio.to_thread(
        stack["store"].leases.release, handle.lease_key, handle.holder, fencing_token=handle.fencing_token
    )

    # The park deadline is the declared one; a duplicate scan before it is
    # a no-op and a due scan wakes exactly once.
    not_due = _timer_waker(stack, clock=lambda: datetime.now(UTC))
    assert await not_due.wake_once() == 0
    waker = _timer_waker(stack, clock=lambda: datetime.now(UTC) + timedelta(seconds=120))
    assert await waker.wake_once() == 1
    assert await waker.wake_once() == 0
    record = await _dispatch_to_state(stack, t_ref, worker, "succeeded")
    assert record.lifecycle_state.value == "succeeded"

    await _write_evidence(
        stack,
        {
            "wait_type": "timer",
            "task_ref": {"id": str(task_uuid), "issuer": "hecate"},
            "lease_probe": "reacquirable-after-park",
            "duplicate_scans": {"before_due": 0, "due": 1, "after": 0},
            "terminal": record.lifecycle_state.value,
            "observed_from": parked_at.isoformat(),
        },
    )
