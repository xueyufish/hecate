"""Real TCP live-input receipts, committed barriers, process recovery and races.

The local tier uses a WAL SQLite file. HECATE_STEP6_POSTGRES_URL selects a
dedicated PostgreSQL schema. Only model/tool outputs are scripted; HTTP auth,
commands, worker dispatch, decisions, action receipts, result-inbox reads and
SQL commits are real. The default-deployment cutover drill extends the same
harness to two builtin deployments: switching the default re-routes only new
submissions, while submitted tasks recover on their frozen deployment and
version across independent worker processes.
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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import AcceptedDecisionRow
from hecate_durable.worker import OutboxRelay
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.channel.api import tasks as tasks_api
from hecate.contracts.execution.capabilities import (
    EnvironmentOwner,
    HarnessOwner,
    OwnershipAxes,
    ToolExecutionPoint,
)
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.database import Base
from hecate.core.deps import get_db
from hecate.enterprise.auth.token import create_access_token
from hecate.execution.deployment_registry import DeploymentRegistry
from hecate.execution.event_relay import PlatformOutboxProjector
from hecate.execution.task_control import TaskControlService
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.audit import AuditLogModel
from hecate.models.organization import OrganizationModel
from hecate.models.run import RunModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.scenarios.tools.managed_platform import serve_over_tcp, stop_tcp_server
from tests.test_execution._agent_child_db import child_environment
from tests.test_execution.conftest import _harness_schema_name, _pg_url_with_schema
from tests.test_execution.test_controller_loop_dispatch import ORG, OUTPUT_SCHEMA, OWNER, WS

pytestmark = pytest.mark.timeout(180)
_REPO = Path(__file__).resolve().parents[2]
_TERMINAL = {"succeeded", "failed", "cancelled", "reconciliation_required"}


class StubRequeueWorker:
    """Leave all actual dispatch claims to the child worker."""

    async def dispatch_once(self, task_ref) -> bool:
        """Retain the submitted task for the fleet worker."""
        return False


@dataclass
class StubWorkerProcess:
    """Track the worker PID separately from Windows' venv launcher PID."""

    launcher: subprocess.Popen
    pid: int | None = None

    def poll(self):
        """Return the owned launcher's exit status."""
        return self.launcher.poll()

    def kill(self):
        """Kill this test's whole process tree, including the venv redirector child."""
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.launcher.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            self.launcher.kill()

    def wait(self, timeout):
        """Wait until the owned process has exited."""
        return self.launcher.wait(timeout)


@pytest.fixture
async def stack(tmp_path, monkeypatch, request):
    """One SQL stack, authenticated TCP API and replaceable worker processes."""
    postgres = os.environ.get("HECATE_STEP6_POSTGRES_URL")
    schema = _harness_schema_name(tmp_path) if postgres else None
    if postgres:
        database_url = postgres.replace("+psycopg", "+asyncpg")
        admin = create_async_engine(database_url)
        try:
            async with admin.begin() as db:
                await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        finally:
            await admin.dispose()
        engine = create_async_engine(database_url, connect_args={"server_settings": {"search_path": schema}})
        store = SqlDurableStore(_pg_url_with_schema(postgres, schema), source="platform")
    else:
        import sqlite3

        location = tmp_path / "live-input.db"
        with sqlite3.connect(location) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        database_url = f"sqlite+aiosqlite:///{location}"
        engine = create_async_engine(database_url, connect_args={"timeout": 30})
        store = SqlDurableStore(f"sqlite:///{location}", source="platform")
    await asyncio.to_thread(store.create_schema)
    async with engine.begin() as db:
        await db.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer, other_workspace = uuid.uuid4(), uuid.uuid4()
    async with sessions() as db:
        db.add(OrganizationModel(id=ORG, name="live-input-org", slug="live-input-org", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="live-input", slug="live-input"))
        db.add(WorkspaceModel(id=other_workspace, org_id=ORG, name="other", slug="other"))
        db.add(UserModel(id=OWNER, email="live-editor@example.com", hashed_password="unused"))
        db.add(UserModel(id=viewer, email="live-viewer@example.com", hashed_password="unused"))
        db.add(WorkspaceMemberModel(user_id=OWNER, workspace_id=WS, role=WorkspaceRole.EDITOR))
        db.add(WorkspaceMemberModel(user_id=OWNER, workspace_id=other_workspace, role=WorkspaceRole.EDITOR))
        db.add(WorkspaceMemberModel(user_id=viewer, workspace_id=WS, role=WorkspaceRole.VIEWER))
        agent = AgentModel(workspace_id=WS, name="live-input-agent")
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
                id=agent.id, agent_id=agent.id, workspace_id=WS, organization_id=ORG, owner_user_id=OWNER
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

    import hecate.execution.task_control as task_control

    monkeypatch.setattr(task_control, "_spawn_background", lambda coroutine: coroutine.close())
    suite = DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core")
    monkeypatch.setattr(tasks_api, "get_durable_suite", lambda: suite)
    app = FastAPI()
    app.include_router(tasks_api.router, prefix="/api")
    app.include_router(tasks_api.commands_router, prefix="/api")
    app.include_router(tasks_api.runs_router, prefix="/api")

    async def request_db():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    server, serving, base_url = await serve_over_tcp(app)
    state = {
        "store": store,
        "sessions": sessions,
        "agent_id": agent_id,
        "durable_dsn": store.engine.url.render_as_string(hide_password=False),
        "schema": schema,
        "database": "postgresql" if postgres else "sqlite",
        "tmp_path": tmp_path,
        "workers": [],
        "logs": [],
        "task": None,
        "trace": tmp_path / "trace.jsonl",
        "observations": [],
        "started_at": datetime.now(UTC).isoformat(),
        "test_id": request.node.nodeid,
        "code_version": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO, text=True).strip(),
        "code_dirty": subprocess.call(["git", "diff", "--quiet", "HEAD"], cwd=_REPO) != 0,
    }
    tokens = {
        "editor": create_access_token(OWNER, ORG, WS, WorkspaceRole.EDITOR.value),
        "viewer": create_access_token(viewer, ORG, WS, WorkspaceRole.VIEWER.value),
        "other": create_access_token(OWNER, ORG, other_workspace, WorkspaceRole.EDITOR.value),
    }
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as client:
            state.update({"client": client, "tokens": tokens})
            yield state
    finally:
        for process in state["workers"]:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.wait, 15)
        await stop_tcp_server(server, serving)
        for log in state["logs"]:
            log.close()
        try:
            await _write_evidence(state)
        finally:
            await engine.dispose()
            store.dispose()
            if schema:
                admin = create_async_engine(database_url)
                try:
                    async with admin.begin() as db:
                        await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                finally:
                    await admin.dispose()


async def _poll(stack, probe, label, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await probe()
        if value:
            return value
        for process in stack["workers"]:
            if process.poll() not in (None, 0) and process.pid not in stack.get("killed", set()):
                raise AssertionError(f"worker {process.pid} exited unexpectedly")
        await asyncio.sleep(0.05)
    logs = [
        path.read_text(encoding="utf-8", errors="replace")[-1200:] for path in stack["tmp_path"].glob("worker-*.log")
    ]
    raise AssertionError(f"Timed out observing {label}: {logs}")


def _trace(stack, event=None, pid=None):
    path = stack["trace"]
    if not path.exists():
        return []
    # A process may be killed during its final append. Ignore only an incomplete
    # last line; every completed line must remain valid JSON.
    lines = path.read_text(encoding="utf-8").split("\n")[:-1]
    rows = [json.loads(line) for line in lines if line]
    return [row for row in rows if (event is None or row["event"] == event) and (pid is None or row["pid"] == pid)]


async def _event(stack, event, *, pid=None, turn=None):
    async def probe():
        return [row for row in _trace(stack, event, pid) if turn is None or row.get("turn") == turn]

    return await _poll(stack, probe, f"{event} from {pid}, turn {turn}")


async def _spawn_worker(
    stack, role, *, hold_turns=(), hold_bind=False, hold_projection=False, mode="reads", lease_ttl=15
):
    ready = stack["tmp_path"] / f"ready-{role}"
    env = child_environment(stack)
    env.update(
        {
            "PYTHONPATH": str(_REPO),
            "HECATE_LIVE_READY": str(ready),
            "HECATE_LIVE_TRACE": str(stack["trace"]),
            "HECATE_LIVE_BARRIERS": str(stack["tmp_path"]),
            "HECATE_LIVE_HOLD_TURNS": json.dumps(hold_turns),
            "HECATE_LIVE_HOLD_BIND": "1" if hold_bind else "0",
            "HECATE_LIVE_HOLD_PROJECTION": "1" if hold_projection else "0",
            "HECATE_LIVE_MODE": mode,
            "HECATE_LIVE_LEASE_TTL": str(lease_ttl),
        }
    )
    log = open(stack["tmp_path"] / f"worker-{role}.log", "w", encoding="utf-8")  # noqa: SIM115
    stack["logs"].append(log)
    process = StubWorkerProcess(
        subprocess.Popen(
            [sys.executable, "-m", "tests.test_execution._live_input_process"],
            cwd=_REPO,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    )
    stack["workers"].append(process)

    async def probe():
        return ready.is_file()

    await _poll(stack, probe, f"worker {role} ready")
    process.pid = int(ready.read_text(encoding="utf-8"))
    return process


async def _kill(stack, process):
    process.kill()
    await asyncio.to_thread(process.wait, 15)
    stack.setdefault("killed", set()).add(process.pid)


def _release(stack, name):
    (stack["tmp_path"] / f"{name}.release").write_text("release", encoding="utf-8")


async def _submit(stack, *, controller=None):
    async with stack["sessions"]() as db:
        result = await TaskControlService(
            db,
            store=stack["store"],
            recorder=stack["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=stack["sessions"],
            worker=StubRequeueWorker(),
        ).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="live-input restart drill",
            agent_id=stack["agent_id"],
            input={
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 6, **(controller or {})},
            },
        )
    stack.update({"task": result.task_ref, "run": result.run_ref, "controller": {"max_turns": 6, **(controller or {})}})
    return result.task_ref


async def _state(stack):
    return await asyncio.to_thread(stack["store"].get_task_state, stack["task"])


async def _wait_state(stack, expected):
    async def probe():
        record = await _state(stack)
        if record.lifecycle_state.value == expected:
            return record
        assert record.lifecycle_state.value not in _TERMINAL, record.extra
        return None

    return await _poll(stack, probe, f"task {expected}")


async def _body(stack, command_id="live-1", content=None):
    return {
        "kind": "provide_input",
        "command_id": command_id,
        "expected_revision": (await _state(stack)).revision,
        "payload_schema_ref": "urn:hecate:task-live-input/1",
        "payload": {"schema_version": 1, "content": content or {"region": "EU"}},
    }


async def _request(stack, body, *, actor="editor", method="POST", path=None):
    response = await stack["client"].request(
        method,
        path or f"/api/tasks/{stack['task'].id}/commands",
        json=body if method == "POST" else None,
        headers={"Authorization": f"Bearer {stack['tokens'][actor]}"},
    )
    stack["observations"].append({"http_method": method, "http_status": response.status_code, "actor": actor})
    return response


async def _delivery(stack):
    return await asyncio.to_thread(stack["store"].list_input_revisions_with_delivery, stack["task"])


def _decision_rows(stack, run_ref: BackendRef | None = None):
    if run_ref is None:
        run_ref = stack["run"]
    with stack["store"].session_factory() as db:
        rows = db.scalars(select(AcceptedDecisionRow).where(AcceptedDecisionRow.run_id == run_ref.id)).all()
        return [
            {
                "step": row.decision_step_id,
                "digest": row.content_digest,
                "input_refs": row.input_refs,
                "action_keys": [action["action_key"] for action in row.actions],
            }
            for row in sorted(rows, key=lambda row: row.decision_step_id)
        ]


async def _decisions(stack, run_ref: BackendRef | None = None):
    return await asyncio.to_thread(_decision_rows, stack, run_ref)


async def _write_evidence(stack):
    directory = Path(os.environ.get("HECATE_LIVE_INPUT_EVIDENCE_DIR", str(stack["tmp_path"])))
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "database": stack["database"],
        "code_version": stack["code_version"],
        "code_dirty": stack["code_dirty"],
        "started_at": stack["started_at"],
        "test_id": stack["test_id"],
        "source_sha256": {
            name: hashlib.sha256((_REPO / name).read_bytes()).hexdigest()
            for name in (
                "tests/test_execution/test_live_input_restart.py",
                "tests/test_execution/_live_input_process.py",
                "src/hecate/execution/task_control.py",
                "src/hecate/execution/task_dispatcher.py",
                "packages/hecate-durable/src/hecate_durable/storage/store.py",
            )
        },
        "worker_pids": [process.pid for process in stack["workers"]],
        "observations": stack["observations"],
        "verified_monetary_cost": None,
        "model_asks": [{key: row[key] for key in ("pid", "turn")} for row in _trace(stack, "model_ask")],
        "business_calls": [{key: row[key] for key in ("pid", "turn")} for row in _trace(stack, "business_call")],
    }
    if stack["task"]:
        record = await _state(stack)
        decisions = await _decisions(stack)
        payload.update(
            {
                "task_ref": stack["task"].to_dict(),
                "run_ref": stack["run"].to_dict(),
                "final_state": record.lifecycle_state.value,
                "controller_limits": stack["controller"],
                "delivery": await _delivery(stack),
                "decisions": [{key: row[key] for key in ("step", "digest", "action_keys")} for row in decisions],
            }
        )
    path = directory / f"{stack['tmp_path'].name}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["database"] == stack["database"]
    assert set(saved["source_sha256"]) >= {
        "tests/test_execution/test_live_input_restart.py",
        "tests/test_execution/_live_input_process.py",
    }
    assert "provided" not in path.read_text(encoding="utf-8") and "wait_token" not in path.read_text(encoding="utf-8")
    print(f"live-input evidence: {path}")


async def test_live_input_received_in_flight_binds_at_next_boundary(stack):
    """The actual TCP response precedes release; only the next ask sees the input."""
    worker = await _spawn_worker(stack, "A", hold_turns=[1])
    await _submit(stack)
    await _event(stack, "model_ask", pid=worker.pid, turn=1)
    body = await _body(stack)
    response = await _request(stack, body)
    assert response.status_code == 201, response.text
    assert response.json()["delivery"]["status"] == "received"
    assert _trace(stack, "model_ask")[0]["provided"] == []
    assert not _trace(stack, "business_call")
    _release(stack, "model-1")
    await _wait_state(stack, "succeeded")
    rows = await _delivery(stack)
    assert len(rows) == 1 and rows[0]["status"] == "consumed"
    assert rows[0]["effective_turn"] == 2
    assert rows[0]["consumed_by"]["decision_step_id"] == "agent-turn-000002"
    assert [row["provided"] for row in _trace(stack, "model_ask")] == [[], [{"region": "EU"}], [{"region": "EU"}]]
    assert [row["region"] for row in _trace(stack, "business_call")] == [None, "EU"]
    replay = await _request(stack, body)
    assert replay.status_code == 201 and replay.json()["delivery"] == rows[0]
    query = await _request(stack, None, method="GET")
    assert query.status_code == 200 and "region" not in query.text


@pytest.mark.parametrize("window", ["received_not_bound", "bound_not_consumed"])
async def test_restart_recovers_both_fault_windows(stack, window):
    """Kill at an observed committed barrier, then recover the same revision and Run."""
    worker_a = await _spawn_worker(stack, "A", hold_turns=[1], hold_bind=window == "bound_not_consumed")
    await _submit(stack)
    await _event(stack, "model_ask", pid=worker_a.pid, turn=1)
    body = await _body(stack)
    response = await _request(stack, body)
    assert response.status_code == 201, response.text
    before = (await _delivery(stack))[0]
    assert before["status"] == "received"
    if window == "bound_not_consumed":
        _release(stack, "model-1")
        await _event(stack, "binding_committed", pid=worker_a.pid)
        before = (await _delivery(stack))[0]
        assert before["status"] == "bound" and before["effective_turn"] == 2
        assert len(_trace(stack, "model_ask")) == len(_trace(stack, "business_call")) == 1
    committed = await _decisions(stack)
    before_state = await _state(stack)
    original_run = await asyncio.to_thread(stack["store"].run_for_task, stack["task"])
    await _kill(stack, worker_a)
    replay = await _request(stack, body)
    assert replay.status_code == 201 and replay.json()["delivery"] == before
    _release(stack, "model-1")
    worker_b = await _spawn_worker(stack, "B")
    await _wait_state(stack, "succeeded")
    after = (await _delivery(stack))[0]
    expected_turn = 2 if window == "bound_not_consumed" else 1
    assert after["revision_id"] == before["revision_id"]
    assert after["effective_turn"] == expected_turn
    assert after["consumed_by"]["decision_step_id"] == f"agent-turn-{expected_turn:06d}"
    assert after["status"] == "consumed"
    assert await asyncio.to_thread(stack["store"].run_for_task, stack["task"]) == original_run
    assert (await _decisions(stack))[: len(committed)] == committed
    assert [row["turn"] for row in _trace(stack, "business_call")] == [1, 2]
    assert [row["turn"] for row in _trace(stack, "model_ask", worker_b.pid)] == list(range(expected_turn, 4))
    assert (await _state(stack)).revision >= before_state.revision
    replay = await _request(stack, body)
    assert replay.status_code == 201 and replay.json()["delivery"] == after
    stack["observations"].append({"fault_window": window, "binding_before": before, "binding_after": after})


async def test_concurrent_redelivery_conflicts_and_stale_revisions(stack):
    """Concurrent identical TCP submissions create one revision; changed/stale requests refuse."""
    worker = await _spawn_worker(stack, "A", hold_turns=[1])
    await _submit(stack)
    await _event(stack, "model_ask", pid=worker.pid, turn=1)
    body = await _body(stack)
    replies = await asyncio.gather(_request(stack, body), _request(stack, body))
    assert [response.status_code for response in replies] == [201, 201], [response.text for response in replies]
    changed = {**body, "payload": {"schema_version": 1, "content": {"region": "APAC"}}}
    conflict = await _request(stack, changed)
    assert conflict.status_code == 422, conflict.text
    stale = await _request(stack, {**body, "command_id": "stale", "expected_revision": 0})
    assert stale.status_code == 201 and stale.json()["state"] == "rejected", stale.text
    assert len(await _delivery(stack)) == 1
    _release(stack, "model-1")
    await _wait_state(stack, "succeeded")
    replay = await _request(stack, body)
    assert replay.status_code == 201 and replay.json()["delivery"]["status"] == "consumed"
    assert len(await _delivery(stack)) == 1


@pytest.mark.parametrize("terminal", ["cancelled", "succeeded"])
async def test_terminal_settlement_and_no_resurrection(stack, terminal):
    """Cancellation or an already-running final answer settles the unused requirement."""
    worker = await _spawn_worker(stack, "A", hold_turns=[1], mode="final" if terminal == "succeeded" else "reads")
    await _submit(stack)
    await _event(stack, "model_ask", pid=worker.pid, turn=1)
    body = await _body(stack)
    response = await _request(stack, body)
    assert response.status_code == 201
    if terminal == "cancelled":
        cancel = await _request(
            stack,
            {
                "kind": "cancel",
                "command_id": "cancel-1",
                "expected_revision": (await _state(stack)).revision,
            },
        )
        assert cancel.status_code == 201 and cancel.json()["state"] == "requested", cancel.text
    _release(stack, "model-1")
    await _wait_state(stack, terminal)
    delivery = (await _delivery(stack))[0]
    assert delivery["status"] == "not_applied"
    assert delivery["settled_reason"] == f"task_{terminal}"
    await _kill(stack, worker)
    replacement = await _spawn_worker(stack, "B")

    async def recovered_cycles():
        cycles = _trace(stack, "cycle", replacement.pid)
        return cycles if len(cycles) >= 2 else None

    await _poll(stack, recovered_cycles, "two replacement-worker recovery cycles")
    for _ in range(2):
        late = await _request(stack, {**body, "command_id": f"late-{uuid.uuid4().hex}"})
        assert late.status_code == 201 and late.json()["state"] == "rejected", late.text
        replay = await _request(stack, body)
        assert replay.status_code == 201 and replay.json()["delivery"] == delivery
    assert (await _state(stack)).lifecycle_state.value == terminal
    assert not _trace(stack, "model_ask", replacement.pid)
    assert not _trace(stack, "business_call", replacement.pid)
    assert len(await _delivery(stack)) == 1


async def test_http_write_and_replay_check_current_authority(stack):
    """Real JWT authentication rechecks membership on writes and settled-command replay."""
    worker = await _spawn_worker(stack, "A", hold_turns=[1])
    await _submit(stack)
    await _event(stack, "model_ask", pid=worker.pid, turn=1)
    body = await _body(stack)
    assert (await _request(stack, body, actor="viewer")).status_code == 403
    assert (await _request(stack, body, actor="other")).status_code == 404
    assert await _delivery(stack) == []
    for key in ("approval_ref", "wait_token", "agent_child_result"):
        invalid = {**body, "payload": {"schema_version": 1, "content": {key: "forged"}}}
        assert (await _request(stack, invalid)).status_code == 422
    assert await _delivery(stack) == []
    assert (await _request(stack, body)).status_code == 201
    async with stack["sessions"]() as db:
        await db.execute(
            update(WorkspaceMemberModel)
            .where(
                WorkspaceMemberModel.user_id == OWNER,
                WorkspaceMemberModel.workspace_id == WS,
            )
            .values(role=WorkspaceRole.VIEWER)
        )
        await db.commit()
    assert (await _request(stack, body)).status_code == 401
    stack["tokens"]["editor"] = create_access_token(OWNER, ORG, WS, WorkspaceRole.VIEWER.value)
    assert (await _request(stack, body)).status_code == 403
    assert len(await _delivery(stack)) == 1
    _release(stack, "model-1")
    await _wait_state(stack, "succeeded")


@pytest.mark.parametrize("limit", ["turn_cap", "deadline"])
async def test_restart_preserves_original_execution_limits(stack, limit):
    """Accepted turns remain spent, and an absolute deadline cannot move on restart."""
    worker = await _spawn_worker(stack, "A", hold_turns=[1], hold_bind=True, mode="endless")
    # The cap case must reach its turn limit before the independent clock
    # limit. Keep the expiry case short and give the cap case recovery headroom.
    deadline = datetime.now(UTC) + timedelta(seconds=25 if limit == "deadline" else 300)
    controller = {"max_turns": 2, "deadline": deadline.isoformat()}
    await _submit(stack, controller=controller)
    await _event(stack, "model_ask", pid=worker.pid, turn=1)
    response = await _request(stack, await _body(stack))
    assert response.status_code == 201
    _release(stack, "model-1")
    await _event(stack, "binding_committed", pid=worker.pid)
    assert len(await _decisions(stack)) == 1
    assert len(_trace(stack, "business_call")) == 1
    await _kill(stack, worker)
    if limit == "deadline":
        while datetime.now(UTC) <= deadline:
            await asyncio.sleep(0.1)
    replacement = await _spawn_worker(stack, "B", mode="endless")
    record = await _wait_state(stack, "failed")
    result = record.extra["result"]
    if limit == "turn_cap":
        assert result["error_category"] == "controller_turn_limit", result
        assert [row["turn"] for row in _trace(stack, "business_call")] == [1, 2]
        assert [row["turn"] for row in _trace(stack, "model_ask", replacement.pid)] == [2]
    else:
        assert "deadline" in result["error_category"], result
        assert not _trace(stack, "model_ask", replacement.pid)
        assert len(_trace(stack, "business_call")) == 1
    assert len(await _decisions(stack)) == (2 if limit == "turn_cap" else 1)
    stack["observations"].append({"limit": limit, "stop_category": result["error_category"]})


async def _advance_projection(stack) -> None:
    """One production outbox → platform projection pass, driven by the test.

    The child worker pumps visibility between reconcile cycles, but it stays
    inside a dispatch while a model barrier holds, so the disconnected
    submitter's initial checkpoint needs one explicit pass of the same
    production projection the workers use.
    """

    relay = OutboxRelay(
        stack["store"].session_factory,
        PlatformOutboxProjector(stack["store"], stack["sessions"]),
        relay_key="platform",
    )
    await relay.pump_once()


async def test_rest_inbox_delivery_survives_worker_restart(stack):
    """The whole chain on one Task/Run: a JWT user submits asynchronously
    with ``rest_inbox`` over real TCP, sends live input while the first
    model call blocks, a worker restart lands between binding and
    consumption, and the original submitter reads the delivered result
    from the saved inbox cursor."""
    headers = {"Authorization": f"Bearer {stack['tokens']['editor']}"}
    response = await stack["client"].post(
        "/api/tasks",
        json={
            "goal": "rest inbox restart drill",
            "agent_id": str(stack["agent_id"]),
            "input": {
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 6},
                "acceptance": {
                    "version": 1,
                    "required_checks": [
                        {"kind": "output_schema", "schema": OUTPUT_SCHEMA},
                        {"kind": "content_contains", "value": "EU"},
                    ],
                },
            },
            "idempotency_key": "rest-inbox-restart-1",
            "result_delivery": "rest_inbox",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    submitted = response.json()
    assert submitted["result_delivery"] == "rest_inbox" and submitted["replayed"] is False
    task_id = uuid.UUID(submitted["task_id"])
    inbox_path = f"/api/tasks/{task_id}/result-inbox"
    stack["task"] = BackendRef(RefKind.TASK, "hecate", str(task_id))
    stack["run"] = BackendRef(RefKind.RUN, "hecate", str(submitted["run_id"]))
    stack["controller"] = {"max_turns": 6}
    original_run = await asyncio.to_thread(stack["store"].run_for_task, stack["task"])
    stack["observations"].append({"step": "http-submit", "http_status": response.status_code})

    # The first model ask blocks; the dispatched task is RUNNING, so one
    # projection pass gives the disconnected submitter an honest checkpoint.
    worker_a = await _spawn_worker(stack, "inbox-a", hold_turns=[1], hold_bind=True)
    await _event(stack, "model_ask", pid=worker_a.pid, turn=1)
    assert _trace(stack, "model_ask", worker_a.pid)[0]["provided"] == []
    await _advance_projection(stack)

    async with httpx.AsyncClient(base_url=stack["client"].base_url, timeout=10, trust_env=False) as subscriber:
        checkpoint = await subscriber.get(inbox_path, headers=headers)
        assert checkpoint.status_code == 200, checkpoint.text
        saved = checkpoint.json()
        assert saved["notifications"] == [] and saved["next_cursor"] is not None
        saved_cursor = saved["next_cursor"]

    # The live input is received in flight; the blocked call keeps the
    # original input.
    body = await _body(stack)
    receipt = await _request(stack, body)
    assert receipt.status_code == 201, receipt.text
    received = receipt.json()["delivery"]
    assert received["status"] == "received", receipt.text
    assert len(_trace(stack, "model_ask")) == 1

    # The revision binds at the committed decision boundary and the worker
    # dies before consuming it. The redelivered command replays the recorded
    # binding; an independent worker recovers the same Task and Run.
    _release(stack, "model-1")
    await _event(stack, "binding_committed", pid=worker_a.pid)
    assert len(_trace(stack, "model_ask")) == len(_trace(stack, "business_call")) == 1
    (before,) = await _delivery(stack)
    assert before["revision_id"] == received["revision_id"]
    assert before["status"] == "bound" and before["effective_turn"] == 2
    committed = await _decisions(stack)
    before_state = await _state(stack)
    await _kill(stack, worker_a)
    replay = await _request(stack, body)
    assert replay.status_code == 201 and replay.json()["delivery"] == before
    worker_b = await _spawn_worker(stack, "inbox-b", hold_projection=True)
    await _wait_state(stack, "succeeded")

    (after,) = await _delivery(stack)
    assert after["revision_id"] == before["revision_id"]
    assert after["status"] == "consumed" and after["effective_turn"] == 2
    assert after["consumed_by"]["decision_step_id"] == "agent-turn-000002"
    assert await asyncio.to_thread(stack["store"].run_for_task, stack["task"]) == original_run
    assert (await _decisions(stack))[: len(committed)] == committed
    # The confirmed business execution stays unique across the restart:
    # one read without the input, one with it.
    assert [row["turn"] for row in _trace(stack, "business_call")] == [1, 2]
    assert [row["region"] for row in _trace(stack, "business_call")] == [None, "EU"]
    assert [row["turn"] for row in _trace(stack, "model_ask", worker_b.pid)] == [2, 3]
    assert (await _state(stack)).revision >= before_state.revision
    replayed_command = await _request(stack, body)
    assert replayed_command.status_code == 201 and replayed_command.json()["delivery"] == after
    result = (await _state(stack)).extra["result"]
    assert result["acceptance"]["status"] == "passed"

    # The original submitter resumes from the saved cursor on a brand-new
    # connection: one stable completion, replayable by ID, gone once the
    # processed cursor is stored.
    async with httpx.AsyncClient(base_url=stack["client"].base_url, timeout=10, trust_env=False) as subscriber:
        from_checkpoint = {"cursor": saved_cursor}
        before_projection = await subscriber.get(inbox_path, headers=headers, params=from_checkpoint)
        assert before_projection.status_code == 200, before_projection.text
        assert before_projection.json()["notifications"] == []
        stack["observations"].append({"step": "before-terminal-projection", "notification_ids": []})
        _release(stack, "projection")

        async def _completion_page():
            page = await subscriber.get(inbox_path, headers=headers, params=from_checkpoint)
            assert page.status_code == 200, page.text
            page_json = page.json()
            if any(note["type"] == "result_ready" for note in page_json["notifications"]):
                return page_json
            return None

        resumed = await _poll(stack, _completion_page, "the completion notification on the saved cursor")
        notes = resumed["notifications"]
        assert [note["type"] for note in notes] == ["result_ready"], notes
        completion = notes[0]
        assert completion["notification_id"] == f"completion:{task_id}"
        assert completion["run_id"] == str(stack["run"].id)
        assert completion["views"]["run_detail"] == f"/api/runs/{stack['run'].id}"
        again = await subscriber.get(inbox_path, headers=headers, params=from_checkpoint)
        assert again.status_code == 200, again.text
        assert [note["notification_id"] for note in again.json()["notifications"]] == [completion["notification_id"]]
        settled = await subscriber.get(inbox_path, headers=headers, params={"cursor": resumed["next_cursor"]})
        assert settled.status_code == 200, settled.text
        assert settled.json()["notifications"] == [] and settled.json()["has_more"] is False

        # The authorized Run detail behind the view link: the structured
        # result was built from the supplemented input and passed the
        # declared output-schema acceptance.
        detail = await subscriber.get(completion["views"]["run_detail"], headers=headers)
        assert detail.status_code == 200, detail.text
        run_body = detail.json()
        assert run_body["projection"]["state"] == "succeeded"
        delivered = json.loads(run_body["projection"]["result_preview"])
        assert delivered == {"answer": "done", "inputs": [body["payload"]["content"]]}, delivered
        acceptance_view = run_body["delivery_acceptance"]
        assert acceptance_view["status"] == "passed", acceptance_view
        assert acceptance_view["checks"] == [
            {"kind": "output_schema", "status": "passed"},
            {"kind": "content_contains", "status": "passed"},
        ]

        # A same-workspace non-submitter has no inbox here; the revoked
        # original submitter cannot replay the saved cursor either.
        foreign = await subscriber.get(
            inbox_path,
            headers={"Authorization": f"Bearer {stack['tokens']['viewer']}"},
        )
        assert foreign.status_code == 404, foreign.text
        async with stack["sessions"]() as db:
            member = await db.scalar(
                select(WorkspaceMemberModel).where(
                    WorkspaceMemberModel.user_id == OWNER, WorkspaceMemberModel.workspace_id == WS
                )
            )
            assert member is not None
            member.deleted = True
            await db.commit()
        revoked = await subscriber.get(inbox_path, headers=headers, params=from_checkpoint)
        assert revoked.status_code == 401, revoked.text

    stack["observations"].append(
        {
            "step": "rest-inbox-restart-delivery",
            "command_receipt": received["status"],
            "binding_before": before["status"],
            "binding_after": after["status"],
            "revision_id": after["revision_id"],
            "effective_turn": after["effective_turn"],
            "notification_ids": [completion["notification_id"]],
            "run_state": run_body["projection"]["state"],
            "acceptance_status": acceptance_view["status"],
        }
    )


async def _state_of(stack, task_ref):
    return await asyncio.to_thread(stack["store"].get_task_state, task_ref)


async def _wait_task_state(stack, task_ref, expected):
    async def probe():
        record = await _state_of(stack, task_ref)
        if record.lifecycle_state.value == expected:
            return record
        assert record.lifecycle_state.value not in _TERMINAL, record.extra
        return None

    return await _poll(stack, probe, f"task {task_ref.id} {expected}", timeout=120)


async def _runs(stack, task_id):
    """Every attempt run of one task with its frozen deployment binding."""
    async with stack["sessions"]() as db:
        rows = (await db.execute(select(RunModel).where(RunModel.task_id == uuid.UUID(str(task_id))))).scalars().all()
        return [
            {
                "run_id": str(row.id),
                "attempt": row.attempt_no,
                "deployment_id": str(row.deployment_id),
                "agent_version_id": (row.execution_snapshot or {}).get("agent_version_id"),
            }
            for row in sorted(rows, key=lambda row: row.attempt_no)
        ]


async def _post_task(stack, *, key, goal):
    """One real authenticated task submission carrying an idempotency key."""
    response = await stack["client"].post(
        "/api/tasks",
        json={
            "goal": goal,
            "agent_id": str(stack["agent_id"]),
            "input": {
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 6},
            },
            "idempotency_key": key,
        },
        headers={"Authorization": f"Bearer {stack['tokens']['editor']}"},
    )
    stack["observations"].append({"step": "http-submit", "key": key, "http_status": response.status_code})
    return response


def _marker_trace(stack, event, marker):
    return [(row["pid"], row["turn"]) for row in _trace(stack, event) if row.get("marker") == marker]


async def test_default_deployment_cutover_routes_new_tasks_and_preserves_submitted_ones(stack):
    """A→B→A default cutover with cross-process recovery over real TCP.

    Two ``builtin/in_process`` deployments of one agent carry distinct
    frozen agent versions whose model config carries a marker the scripted
    doubles actually observe. Switching the default re-routes only NEW
    submissions: a task submitted under the previous default is recovered
    by an independent worker on its original Run with its frozen deployment
    and version, the same idempotency key replays the original Task/Run
    instead of re-routing, a new key binds to the new default and really
    executes under its marker, and every default change lands in the audit
    log.
    """
    # -- Two deployments, one agent, distinct frozen versions. -------------
    async with stack["sessions"]() as db:
        deployment_a = (
            await db.execute(select(AgentDeploymentModel).where(AgentDeploymentModel.is_default.is_(True)))
        ).scalar_one()
        version_a = await db.get(AgentVersionModel, deployment_a.agent_version_id)
        # The marker rides each version's frozen model_config: the real port
        # config hands it to the stub, so the doubles observe which agent
        # version actually drives every ask and tool call. Frozen before any
        # run exists — later edits would fail the run revalidation gate.
        version_a.config_snapshot = {
            "tools": ["query_inventory"],
            "model_config": {"model": "stub-model", "marker": "deploy-a"},
        }
        version_b = AgentVersionModel(
            agent_id=deployment_a.agent_id,
            version=2,
            config_snapshot={
                "tools": ["query_inventory"],
                "model_config": {"model": "stub-model", "marker": "deploy-b"},
            },
            content_hash="b" * 64,
        )
        db.add(version_b)
        await db.flush()
        deployment_b = await DeploymentRegistry(db).register(
            deployment_a.agent_id,
            BackendType.BUILTIN,
            AccessMode.IN_PROCESS,
            OwnershipAxes(
                harness=HarnessOwner.HECATE,
                environment=EnvironmentOwner.NONE,
                tool_execution=ToolExecutionPoint.HECATE_GATEWAY,
            ),
            workspace_id=WS,
            agent_version_id=version_b.id,
            registered_by=OWNER,
        )
        agent_id = deployment_a.agent_id
        await db.commit()
    deployment_a_id, deployment_b_id = deployment_a.id, deployment_b.id
    version_a_id, version_b_id = version_a.id, version_b.id
    stack["observations"].append(
        {
            "step": "cutover-setup",
            "deployment_a": str(deployment_a_id),
            "deployment_b": str(deployment_b_id),
            "version_a": str(version_a_id),
            "version_b": str(version_b_id),
        }
    )

    # -- A is the default: submit, first receipt persists, kill at the -----
    # -- next decision boundary. ------------------------------------------
    # Give the worker time to reach the injected boundary even under runner
    # contention. Recovery still waits for the killed worker's real lease.
    lease_ttl = 45
    worker_a = await _spawn_worker(stack, "cut-a", hold_turns=[2], lease_ttl=lease_ttl)
    submit_a = await _post_task(stack, key="cutover-a-1", goal="cutover drill on A")
    assert submit_a.status_code == 201, submit_a.text
    submitted_a = submit_a.json()
    assert submitted_a["replayed"] is False, submit_a.text
    task_a = BackendRef(RefKind.TASK, "hecate", str(submitted_a["task_id"]))
    run_a = BackendRef(RefKind.RUN, "hecate", str(submitted_a["run_id"]))
    stack["task"], stack["run"], stack["controller"] = task_a, run_a, {"max_turns": 6}
    original_rows = await _runs(stack, task_a.id)
    assert original_rows == [
        {
            "run_id": run_a.id,
            "attempt": 1,
            "deployment_id": str(deployment_a_id),
            "agent_version_id": str(version_a_id),
        }
    ]
    original_record = await asyncio.to_thread(stack["store"].run_for_task, task_a)

    await _event(stack, "model_ask", pid=worker_a.pid, turn=1)
    assert _trace(stack, "model_ask", worker_a.pid)[0]["marker"] == "deploy-a"
    # Turn two's ask only happens after turn one's decision and its tool
    # receipt are committed — the worker is killed exactly at that boundary.
    await _event(stack, "model_ask", pid=worker_a.pid, turn=2)
    committed = await _decisions(stack)
    assert len(committed) == 1 and len(committed[0]["action_keys"]) == 1, committed
    list_run_actions = stack["store"].list_run_actions
    (first_receipt,) = await asyncio.to_thread(list_run_actions, run_a)
    assert first_receipt["intent"]["action_name"] == "query_inventory", first_receipt
    assert first_receipt["last_outcome"]["outcome"] == "succeeded", first_receipt
    assert first_receipt["last_outcome"]["result_digest"], first_receipt
    assert len(_trace(stack, "business_call", worker_a.pid)) == 1
    before_state = await _state(stack)
    await _kill(stack, worker_a)

    # -- Default switches to B: the same key replays the original, ---------
    # -- a new key binds to B. ---------------------------------------------
    async with stack["sessions"]() as db:
        await DeploymentRegistry(db).set_default(agent_id, deployment_b_id, workspace_id=WS, actor_id=OWNER)
        await db.commit()
    replay_a = await _post_task(stack, key="cutover-a-1", goal="cutover drill on A")
    # A replay is not a new resource: the API returns the original
    # association with 200 instead of the fresh-submission 201.
    assert replay_a.status_code == 200, replay_a.text
    replayed_a = replay_a.json()
    assert replayed_a["replayed"] is True, replay_a.text
    assert replayed_a["task_id"] == submitted_a["task_id"], replay_a.text
    assert replayed_a["run_id"] == submitted_a["run_id"], replay_a.text
    assert await _runs(stack, task_a.id) == original_rows

    submit_b = await _post_task(stack, key="cutover-b-1", goal="cutover drill on B")
    assert submit_b.status_code == 201, submit_b.text
    submitted_b = submit_b.json()
    assert submitted_b["replayed"] is False and submitted_b["task_id"] != submitted_a["task_id"]
    task_b = BackendRef(RefKind.TASK, "hecate", str(submitted_b["task_id"]))
    run_b = BackendRef(RefKind.RUN, "hecate", str(submitted_b["run_id"]))
    original_b_rows = await _runs(stack, task_b.id)
    assert original_b_rows == [
        {
            "run_id": run_b.id,
            "attempt": 1,
            "deployment_id": str(deployment_b_id),
            "agent_version_id": str(version_b_id),
        }
    ]

    # -- One independent worker recovers A's task and executes B's task. ---
    worker_b = await _spawn_worker(stack, "cut-b", lease_ttl=lease_ttl)
    await _wait_task_state(stack, task_a, "succeeded")
    await _wait_task_state(stack, task_b, "succeeded")

    # A's task recovered on the SAME run under its frozen deployment and
    # version; the committed decision prefix is unchanged and no confirmed
    # tool call repeats (turn one ran on the killed worker, turn two on the
    # recovery worker — both observed under A's marker).
    assert await asyncio.to_thread(stack["store"].run_for_task, task_a) == original_record
    recovered = await _decisions(stack, run_a)
    assert len(recovered) == 3, recovered
    assert recovered[: len(committed)] == committed
    actions_a = {action["action_key"]: action for action in await asyncio.to_thread(list_run_actions, run_a)}
    assert len(actions_a) == 2
    assert actions_a[first_receipt["action_key"]] == first_receipt
    assert all(row["input_refs"]["agent_version"] == str(version_a_id) for row in recovered)
    assert all(row["input_refs"]["agent_version_no"] == 1 for row in recovered)
    assert len({key for row in recovered for key in row["action_keys"]}) == 2
    assert (await _state(stack)).revision >= before_state.revision

    # B's task really executed under B's frozen version.
    executed_b = await _decisions(stack, run_b)
    assert len(executed_b) == 3, executed_b
    assert all(row["input_refs"]["agent_version"] == str(version_b_id) for row in executed_b)
    assert all(row["input_refs"]["agent_version_no"] == 2 for row in executed_b)
    assert len({key for row in executed_b for key in row["action_keys"]}) == 2

    assert _marker_trace(stack, "business_call", "deploy-a") == [
        (worker_a.pid, 1),
        (worker_b.pid, 2),
    ]
    assert _marker_trace(stack, "business_call", "deploy-b") == [
        (worker_b.pid, 1),
        (worker_b.pid, 2),
    ]
    assert _marker_trace(stack, "model_ask", "deploy-a") == [
        (worker_a.pid, 1),
        (worker_a.pid, 2),
        (worker_b.pid, 2),
        (worker_b.pid, 3),
    ]
    assert _marker_trace(stack, "model_ask", "deploy-b") == [
        (worker_b.pid, 1),
        (worker_b.pid, 2),
        (worker_b.pid, 3),
    ]

    # -- Default switches back to A: new submissions follow it again, ------
    # -- while A's and B's earlier bindings stay queryable. ----------------
    await _kill(stack, worker_b)
    async with stack["sessions"]() as db:
        await DeploymentRegistry(db).set_default(agent_id, deployment_a_id, workspace_id=WS, actor_id=OWNER)
        await db.commit()
    submit_c = await _post_task(stack, key="cutover-a-2", goal="cutover drill back on A")
    assert submit_c.status_code == 201, submit_c.text
    submitted_c = submit_c.json()
    assert submitted_c["replayed"] is False
    task_c = BackendRef(RefKind.TASK, "hecate", str(submitted_c["task_id"]))
    run_c = BackendRef(RefKind.RUN, "hecate", str(submitted_c["run_id"]))
    assert await _runs(stack, task_c.id) == [
        {
            "run_id": run_c.id,
            "attempt": 1,
            "deployment_id": str(deployment_a_id),
            "agent_version_id": str(version_a_id),
        }
    ]
    worker_c = await _spawn_worker(stack, "cut-c", lease_ttl=lease_ttl)
    await _wait_task_state(stack, task_c, "succeeded")
    assert _marker_trace(stack, "business_call", "deploy-a") == [
        (worker_a.pid, 1),
        (worker_b.pid, 2),
        (worker_c.pid, 1),
        (worker_c.pid, 2),
    ]
    assert _marker_trace(stack, "model_ask", "deploy-a") == [
        (worker_a.pid, 1),
        (worker_a.pid, 2),
        (worker_b.pid, 2),
        (worker_b.pid, 3),
        (worker_c.pid, 1),
        (worker_c.pid, 2),
        (worker_c.pid, 3),
    ]
    executed_c = await _decisions(stack, run_c)
    assert len(executed_c) == 3
    assert all(row["input_refs"]["agent_version"] == str(version_a_id) for row in executed_c)

    # History stays queryable: A's original run, B's task and the audit
    # trail of both default changes.
    assert await _runs(stack, task_a.id) == original_rows
    assert await _runs(stack, task_b.id) == original_b_rows
    await _advance_projection(stack)
    headers = {"Authorization": f"Bearer {stack['tokens']['editor']}"}
    for run_ref, deployment_id in ((run_a, deployment_a_id), (run_b, deployment_b_id)):
        response = await stack["client"].get(f"/api/runs/{run_ref.id}", headers=headers)
        assert response.status_code == 200, response.text
        history = response.json()
        assert history["run_id"] == run_ref.id
        assert history["deployment_id"] == str(deployment_id)
        assert history["projection"]["state"] == "succeeded"
    assert len(_trace(stack, "business_call")) == 6
    assert len(_trace(stack, "model_ask")) == 10

    async with stack["sessions"]() as db:
        audits = (
            (
                await db.execute(
                    select(AuditLogModel)
                    .where(
                        AuditLogModel.action.in_(("AGENT_DEPLOYMENT_REGISTERED", "AGENT_DEPLOYMENT_DEFAULT_SET")),
                        AuditLogModel.workspace_id == WS,
                        AuditLogModel.resource_type == "agent_deployment",
                    )
                    .order_by(AuditLogModel.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert [(row.action, row.resource_id, row.user_id, row.success) for row in audits] == [
        ("AGENT_DEPLOYMENT_REGISTERED", deployment_b_id, OWNER, True),
        ("AGENT_DEPLOYMENT_DEFAULT_SET", deployment_b_id, OWNER, True),
        ("AGENT_DEPLOYMENT_DEFAULT_SET", deployment_a_id, OWNER, True),
    ]
    assert all(row.metadata_["agent_id"] == str(agent_id) for row in audits)
    assert audits[0].metadata_["agent_version_id"] == str(version_b_id)

    stack["observations"].append(
        {
            "step": "cutover-complete",
            "worker_pids": [worker_a.pid, worker_b.pid, worker_c.pid],
            "deployments": {"a": str(deployment_a_id), "b": str(deployment_b_id)},
            "versions": {"a": str(version_a_id), "b": str(version_b_id)},
            "tasks": {
                "a_original": {"task": str(task_a.id), "run": str(run_a.id)},
                "b_new": {"task": str(task_b.id), "run": str(run_b.id)},
                "a_after_rollback": {"task": str(task_c.id), "run": str(run_c.id)},
            },
            "business_calls": {
                "deploy-a": len(_marker_trace(stack, "business_call", "deploy-a")),
                "deploy-b": len(_marker_trace(stack, "business_call", "deploy-b")),
            },
            "audits": [
                {
                    "id": str(row.id),
                    "action": row.action,
                    "deployment_id": str(row.resource_id),
                    "actor_id": str(row.user_id),
                    "success": row.success,
                }
                for row in audits
            ],
            "recovered_decision_digest": committed[0]["digest"],
            "first_action_result_digest": first_receipt["last_outcome"]["result_digest"],
            "replayed_same_task_run": True,
        }
    )
