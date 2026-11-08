"""Real TCP live-input receipts, committed barriers, process recovery and races.

The local tier uses a WAL SQLite file. HECATE_STEP6_POSTGRES_URL selects a
dedicated PostgreSQL schema. Only model/tool outputs are scripted; HTTP auth,
commands, worker dispatch, decisions, action receipts and SQL commits are real.
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
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.channel.api import tasks as tasks_api
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.database import Base
from hecate.core.deps import get_db
from hecate.enterprise.auth.token import create_access_token
from hecate.execution.task_control import TaskControlService
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.scenarios.tools.managed_platform import serve_over_tcp, stop_tcp_server
from tests.test_execution._agent_child_db import child_environment
from tests.test_execution.conftest import _harness_schema_name, _pg_url_with_schema
from tests.test_execution.test_controller_loop_dispatch import ORG, OWNER, WS

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


async def _spawn_worker(stack, role, *, hold_turns=(), hold_bind=False, mode="reads"):
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
            "HECATE_LIVE_MODE": mode,
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


def _decision_rows(stack):
    with stack["store"].session_factory() as db:
        rows = db.scalars(select(AcceptedDecisionRow).where(AcceptedDecisionRow.run_id == stack["run"].id)).all()
        return [
            {
                "step": row.decision_step_id,
                "digest": row.content_digest,
                "input_refs": row.input_refs,
                "action_keys": [action["action_key"] for action in row.actions],
            }
            for row in sorted(rows, key=lambda row: row.decision_step_id)
        ]


async def _decisions(stack):
    return await asyncio.to_thread(_decision_rows, stack)


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
