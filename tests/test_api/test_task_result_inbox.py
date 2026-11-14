"""The REST submitter's asynchronous result inbox (P-B ``rest_inbox``).

Part A runs the production API dependency chain over a real SQL durable
store and the platform event projection: submission binding, source
authorization, notification mapping, completion collapse, re-delivery
stability and cursor integrity.

Part B is one real-TCP acceptance drill: submit asynchronously over HTTP
with the deterministic model blocked on a barrier, disconnect the client,
let execution finish, reconnect, resume from the saved cursor and read
the authorized Run delivery facts. Only the model/tool seams are
scripted; HTTP, JWT auth, dispatch, commands, ledger receipts and SQL
commits are real.

Part C closes the same loop with the host-enforced field provenance
profile active: a trusted read receipt sources the protected write and
the delivery is accepted; a guessed value is intercepted by the real
guard with zero business writes and completes only after one idempotent
REST ``provide_input``; and the inbox's authorization boundaries and
payload minimization hold end to end.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import subprocess
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from hecate_durable.worker import OutboxRelay
from hecate_runtime.action_ledger import tool_arguments_digest
from hecate_runtime.ports import StubRuntimePort
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.channel.api import tasks as tasks_api
from hecate.channel.api.tasks import router, runs_router
from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.database import Base
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.enterprise.auth.token import create_access_token
from hecate.execution.event_relay import PlatformOutboxProjector
from hecate.execution.governance_events import RUN_TERMINAL, PlatformEventService
from hecate.execution.task_control import TaskControlService, TaskControlValidationError, _background_tasks
from hecate.execution.task_input import TASK_USER_INPUT_SCHEMA_REF
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_trusted_field_platform as tdriver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.tools.managed_platform import serve_over_tcp, stop_tcp_server
from tests.test_execution.test_controller_loop_dispatch import ORG, OWNER, WS, _no_guardrails, _ScriptedAssembly

_REPO = Path(__file__).resolve().parents[2]


def _acceptance_record(task_id, run_id, workspace_id, content: str) -> dict:
    """A display-valid delivery acceptance record (integrity digest intact)."""
    record = {
        "checker_version": "delivery-acceptance/1",
        "version": 2,
        "declaration_sha256": "a" * 64,
        "status": "passed",
        "checks": [{"kind": "content_contains", "status": "passed"}],
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "task_ref": {"issuer": "hecate", "id": str(task_id)},
        "run_ref": {"issuer": "hecate", "id": str(run_id)},
        "evaluated_by": {
            "host": "test",
            "workspace_id": str(workspace_id),
            "submitted_by": "tester",
            "principal": "platform-system",
        },
        "final_decision": {"decision_step_id": "agent-turn-000002", "content_digest": "c" * 64},
    }
    body = copy.deepcopy(record)
    record["record_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    return record


# ---------------------------------------------------------------------------
# Part A: production API chain over real durable + projection stores
# ---------------------------------------------------------------------------


@pytest.fixture
async def env(tmp_path, monkeypatch):
    """One shared SQLite file holding the durable and platform schemas, real
    services bound to it, background dispatch absorbed, relay observed."""
    import sqlite3 as _sqlite3

    from hecate.core.auth_context import AuthContext
    from hecate.models.user import UserModel as _UserModel

    db_file = tmp_path / "result-inbox.db"
    with _sqlite3.connect(db_file) as raw:
        raw.execute("PRAGMA journal_mode=WAL")
    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})

    from sqlalchemy import event as sa_event

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _busy(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA busy_timeout=30000")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    ws_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    other_id = uuid.uuid4()
    async with sessions() as db:
        db.add(OrganizationModel(id=org_id, name="inbox-org", slug=f"inbox-{org_id.hex[:12]}", owner_id=user_id))
        db.add(WorkspaceModel(id=ws_id, org_id=org_id, name="inbox-ws", slug=f"inbox-ws-{ws_id.hex[:12]}"))
        db.add(_UserModel(id=user_id, email="inbox-submit@example.com", hashed_password="unused"))
        db.add(
            OrganizationModel(
                id=other_org_id, name="other-org", slug=f"other-{other_org_id.hex[:12]}", owner_id=user_id
            )
        )
        other = WorkspaceModel(id=other_id, org_id=other_org_id, name="other-ws", slug=f"other-ws-{other_id.hex[:12]}")
        db.add(other)
        agent = AgentModel(workspace_id=ws_id, name="result-inbox-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub", "tools": ["query_inventory"]},
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

    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coroutine: coroutine.close())
    monkeypatch.setattr(
        tasks_api,
        "get_durable_suite",
        lambda: DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core"),
    )
    admin_ctx = AuthContext(
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )
    records = {
        "db": None,
        "sessions": sessions,
        "store": store,
        "agent_id": agent.id,
        "other": other,
        "admin_ctx": admin_ctx,
        "holder": {"ctx": admin_ctx},
        "calls": [],
        "platform_factory": sessions,
    }

    class StubCountingRelay:
        """Detect accidental projection from a read (never consumes rows)."""

        async def pump_once(self) -> None:
            records["calls"].append("relay")

    monkeypatch.setattr(tasks_api, "_worker_state", SimpleNamespace(durable_outbox_relay=StubCountingRelay()))
    for name in ("get_task_state", "get_task_input"):
        original = getattr(store, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            records["calls"].append("store." + _name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observed)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(runs_router, prefix="/api")

    async def request_db():
        async with sessions() as session:
            yield session

    async def swapped_auth():
        return records["holder"]["ctx"]

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    try:
        # raise_app_exceptions=False: a failing read surfaces as HTTP 500,
        # exactly like a deployed server would answer.
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
        ) as client:
            records["client"] = client
            yield records
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        store.dispose()


async def _submit(env, *, key: str, result_delivery: str | None = "rest_inbox", **overrides) -> dict:
    body = {
        "goal": f"inbox task {key}",
        "agent_id": str(env["agent_id"]),
        "input": {
            "messages": [{"role": "user", "content": f"secret-input-{key}"}],
            "controller_loop": True,
        },
        "idempotency_key": key,
        "result_delivery": result_delivery,
    }
    body.update(overrides)
    response = await env["client"].post("/api/tasks", json=body)
    assert response.status_code in (200, 201), response.text
    return {"status_code": response.status_code, **response.json()}


async def _pump(env) -> int:
    """The production outbox → platform projection, driven explicitly."""
    relay = OutboxRelay(
        env["store"].session_factory,
        PlatformOutboxProjector(env["store"], env["platform_factory"]),
        relay_key="inbox-test",
    )
    return await relay.pump_once()


async def _apply(env, t_ref, target, *, extra_update=None, terminal_payload=None, **kwargs):
    record = await asyncio.to_thread(env["store"].get_task_state, t_ref)
    await asyncio.to_thread(
        env["store"].apply_task_state,
        t_ref,
        target,
        expected_revision=record.revision,
        **({"extra_update": extra_update} if extra_update else {}),
        **({"terminal_payload": terminal_payload} if terminal_payload is not None else {}),
        **kwargs,
    )


async def _inbox(env, task_id, *, cursor=None, limit=None) -> dict:
    params = {}
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = limit
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _t_ref(env, task_id) -> BackendRef:
    return BackendRef(RefKind.TASK, "hecate", str(task_id))


async def _bound_task(env, key) -> tuple[uuid.UUID, BackendRef, BackendRef]:
    submitted = await _submit(env, key=key)
    task_id = uuid.UUID(submitted["task_id"])
    run_id = uuid.UUID(submitted["run_id"])
    return task_id, _t_ref(env, task_id), BackendRef(RefKind.RUN, "hecate", str(run_id))


def _wait(kind, marker="wait-marker-1", consumed=False):
    return {
        "wake_kind": kind,
        "contract_ref": {
            "kind": "input",
            "tool": "query_inventory",
            "execution_id": "synthetic-execution",
            "arguments_digest": "a" * 64,
            "contract": {"fields": ["region"]},
        },
        "wait_token": marker,
        "wait_expires_at": None,
        "consumed": consumed,
    }


async def test_submit_binds_source_and_replays_preserve_association(env):
    """The binding is server-derived, persists with the submission, replays
    reuse the same association, and changing the option under one key is a
    request conflict while legacy requests keep their digest semantics."""
    first = await _submit(env, key="bind-1")
    assert first["status_code"] == 201 and first["replayed"] is False
    assert first["result_delivery"] == "rest_inbox"
    t_ref = _t_ref(env, first["task_id"])
    saved = await asyncio.to_thread(env["store"].get_task_input, t_ref)
    assert saved["result_delivery"] == {
        "mode": "rest_inbox",
        "workspace_id": str(env["admin_ctx"].workspace_id),
        "submitted_by": str(env["admin_ctx"].user_id),
    }
    # The binding lives outside the caller-controlled message body.
    assert "result_delivery" not in saved["messages"][0]

    replay = await _submit(env, key="bind-1")
    assert replay["status_code"] == 200 and replay["replayed"] is True
    assert replay["task_id"] == first["task_id"] and replay["run_id"] == first["run_id"]
    assert replay["result_delivery"] == "rest_inbox"

    # The option is part of the request digest: dropping it under the same
    # key is a different request (409), not a silent downgrade.
    response = await env["client"].post(
        "/api/tasks",
        json={
            "goal": "inbox task bind-1",
            "agent_id": str(env["agent_id"]),
            "input": {
                "messages": [{"role": "user", "content": "secret-input-bind-1"}],
                "controller_loop": True,
            },
            "idempotency_key": "bind-1",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # Legacy submissions (no option) keep their digest and replay verbatim.
    legacy = await _submit(env, key="legacy-1", result_delivery=None)
    assert legacy["status_code"] == 201
    # Unbound submissions keep the exact legacy response shape: absence is
    # the not-bound signal, never an explicit null.
    assert "result_delivery" not in legacy
    saved_legacy = await asyncio.to_thread(env["store"].get_task_input, _t_ref(env, legacy["task_id"]))
    assert "result_delivery" not in saved_legacy
    legacy_replay = await _submit(env, key="legacy-1", result_delivery=None)
    assert legacy_replay["status_code"] == 200 and legacy_replay["replayed"] is True
    assert legacy_replay["task_id"] == legacy["task_id"]


async def test_result_delivery_option_gating(env):
    """The option exists only for async, non-stream submissions."""
    for overrides in ({"wait": True}, {"stream": True}):
        response = await env["client"].post(
            "/api/tasks",
            json={
                "goal": "gated",
                "agent_id": str(env["agent_id"]),
                "input": {"messages": [{"role": "user", "content": "x"}]},
                "idempotency_key": f"gated-{sorted(overrides)}",
                "result_delivery": "rest_inbox",
                **overrides,
            },
        )
        assert response.status_code == 422, response.text
    response = await env["client"].post(
        "/api/tasks",
        json={
            "goal": "unknown option",
            "agent_id": str(env["agent_id"]),
            "input": {"messages": [{"role": "user", "content": "x"}]},
            "idempotency_key": "unknown-option",
            "result_delivery": "webhook",
        },
    )
    assert response.status_code == 422, response.text
    # The service-level gate also covers direct callers past the API model.
    service = TaskControlService(
        None,
        store=env["store"],
        recorder=env["store"],
        backend="postgres",
        ledger_source="core",
    )
    with pytest.raises(TaskControlValidationError):
        await service.submit(
            workspace_id=env["admin_ctx"].workspace_id,
            user_id=env["admin_ctx"].user_id,
            goal="direct",
            agent_id=env["agent_id"],
            input={"messages": [{"role": "user", "content": "x"}]},
            wait=True,
            result_delivery="rest_inbox",
        )


async def test_inbox_authorization_and_invisibility(env):
    """Every read re-verifies workspace, visibility, VIEWER floor and the
    submission-time source submitter; denied reads touch nothing."""
    bound = await _submit(env, key="authz-1")
    unbound = await _submit(env, key="authz-2", result_delivery=None)
    foreigner = uuid.uuid4()
    missing = uuid.uuid4()
    task_id = uuid.UUID(bound["task_id"])

    # The submitter themselves, at the EDITOR and the VIEWER floor.
    page = await _inbox(env, task_id)
    assert page["result_delivery"] == "rest_inbox" and page["notifications"] == []
    env["holder"]["ctx"] = replace(env["admin_ctx"], role=WorkspaceRole.VIEWER)
    assert (await _inbox(env, task_id))["notifications"] == []

    # Same workspace, different identity: one indistinguishable 404.
    env["holder"]["ctx"] = replace(env["admin_ctx"], user_id=foreigner)
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox")
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"
    unavailable = response.json()

    env["holder"]["ctx"] = replace(env["admin_ctx"], auth_method="api_key", api_key_scope="workspace")
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox", params={"cursor": "malformed"})
    assert response.status_code == 404 and response.json() == unavailable

    # Same identity, different workspace.
    env["holder"]["ctx"] = replace(env["admin_ctx"], workspace_id=env["other"].id, org_id=env["other"].org_id)
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox")
    assert response.status_code == 404, response.text

    # Gate order: workspace requirement and role precede any lookup.
    env["holder"]["ctx"] = replace(env["admin_ctx"], workspace_id=None)
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox")
    assert response.status_code == 403 and response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    env["holder"]["ctx"] = replace(env["admin_ctx"], role=None)
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox")
    assert response.status_code == 403 and response.json()["detail"]["error"]["code"] == "FORBIDDEN"

    # A task without the binding has no inbox even for its own submitter;
    # missing and soft-deleted tasks are the same 404.
    env["holder"]["ctx"] = env["admin_ctx"]
    response = await env["client"].get(f"/api/tasks/{uuid.UUID(unbound['task_id'])}/result-inbox")
    assert response.status_code == 404, response.text
    response = await env["client"].get(f"/api/tasks/{missing}/result-inbox")
    assert response.status_code == 404 and response.json() == unavailable
    async with env["sessions"]() as db:
        task_row = await db.get(TaskModel, task_id)
        task_row.deleted = True
        await db.commit()
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox")
    assert response.status_code == 404, response.text
    async with env["sessions"]() as db:
        (await db.get(TaskModel, task_id)).deleted = False
        (await db.get(RunModel, uuid.UUID(bound["run_id"]))).deleted = True
        await db.commit()
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox")
    assert response.status_code == 404 and response.json() == unavailable


@pytest.mark.parametrize("profile", ["api_key", "plain_task"])
async def test_inbox_requires_the_jwt_controller_profile(env, profile):
    """Opt-in requires the named source and execution profile."""
    if profile == "api_key":
        env["holder"]["ctx"] = replace(env["admin_ctx"], auth_method="api_key", api_key_scope="workspace")
    response = await env["client"].post(
        "/api/tasks",
        json={
            "goal": "unsupported inbox profile",
            "agent_id": str(env["agent_id"]),
            "input": {
                "messages": [{"role": "user", "content": "check stock"}],
                "controller_loop": profile != "plain_task",
            },
            "result_delivery": "rest_inbox",
        },
    )
    assert response.status_code == 422, response.text


async def test_result_ready_waits_for_terminal_projection(env, monkeypatch):
    """Recover from a terminal projection outage and a lost projection acknowledgement."""
    task_id, t_ref, _run = await _bound_task(env, key="projected-result")
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    await _apply(
        env,
        t_ref,
        TaskLifecycleState.SUCCEEDED,
        terminal_payload={"status": "succeeded", "content": "projected answer"},
    )
    original = PlatformOutboxProjector.__call__
    failures = iter(["before_projection", "after_projection"])

    async def intermittent(projector, envelope):
        fault = next(failures, None) if envelope["payload"].get("event_type") == "run_terminal" else None
        if fault == "before_projection":
            raise RuntimeError("injected projection outage")
        await original(projector, envelope)
        if fault == "after_projection":
            raise RuntimeError("injected lost projection acknowledgement")

    monkeypatch.setattr(PlatformOutboxProjector, "__call__", intermittent)
    await _pump(env)
    pending = await _inbox(env, task_id)
    assert pending["notifications"] == [] and pending["next_cursor"] is not None
    await _pump(env)
    first = await _inbox(env, task_id, cursor=pending["next_cursor"])
    assert [note["type"] for note in first["notifications"]] == ["result_ready"]
    await _pump(env)
    assert await _inbox(env, task_id, cursor=pending["next_cursor"]) == first
    assert (await _inbox(env, task_id, cursor=first["next_cursor"]))["notifications"] == []


async def test_historical_system_wait_cannot_become_current_human_wait(env):
    """A page boundary cannot give an old timer the current wait's identity."""
    task_id, t_ref, _run = await _bound_task(env, key="page-wait")
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    await _apply(env, t_ref, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("timer")})
    await _pump(env)
    before = await _inbox(env, task_id)
    assert before["notifications"] == []
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    await _apply(env, t_ref, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("provide_input")})
    await _pump(env)
    current = await _inbox(env, task_id, cursor=before["next_cursor"], limit=1)
    first = await _inbox(env, task_id, limit=1)
    assert first["notifications"] == current["notifications"]


async def test_inbox_read_only_query_never_projects_or_dispatches(env):
    """Reading never pumps the relay, never dispatches and writes nothing."""
    bound = await _submit(env, key="ro-1")
    task_id = uuid.UUID(bound["task_id"])
    t_ref = _t_ref(env, task_id)
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    await _apply(env, t_ref, TaskLifecycleState.SUCCEEDED, terminal_payload={"status": "succeeded", "content": "done"})
    assert await _pump(env) >= 3

    async def snapshot():
        result = {}
        async with env["sessions"]() as db:
            for model in (TaskModel, RunModel):
                table = model.__table__
                rows = await db.execute(select(table).order_by(*table.primary_key.columns))
                result[table.name] = [dict(row) for row in rows.mappings()]
        with env["store"].engine.connect() as conn:
            for table in DurableBase.metadata.sorted_tables:
                rows = conn.execute(select(table).order_by(*table.primary_key.columns))
                result[table.name] = [dict(row) for row in rows.mappings()]
        return result

    before = await snapshot()
    for _ in range(3):
        page = await _inbox(env, task_id)
        assert [note["type"] for note in page["notifications"]] == ["result_ready"]
    assert "relay" not in env["calls"], env["calls"]
    assert await snapshot() == before, "an inbox read must not write durable or platform facts"


async def test_notification_categories_completion_collapse_and_redelivery(env):
    """Result, needs-attention and stop are separate; one completion is one
    notification despite task_state + run_terminal; re-delivery keeps the
    same ids; resolved waits, timers and attempt facts never mislead."""
    task_id, t_ref, r_ref = await _bound_task(env, key="map-1")

    # The task parks on an approval wait → open approval notification.
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from hecate.execution.approval_wait import mint_pending_approval_binding

    async with env["sessions"]() as db:
        ledger = SqlApprovalLedger(db)
        approval_ref, binding = mint_pending_approval_binding(
            ledger,
            task_id=task_id,
            actor=str(env["admin_ctx"].user_id),
            agent_principal=str(env["agent_id"]),
            tool_name="query_inventory",
            arguments={"sku": "test"},
        )
        await ledger.save(binding)
        await db.commit()
    approval_wait = _wait("resume")
    approval_wait["wait_expires_at"] = binding.expires_at.isoformat()
    approval_wait["contract_ref"] = {
        "approval_ref": approval_ref,
        "tool_name": "query_inventory",
        "arguments_digest": binding.arguments_digest,
        "target_resources": list(binding.target_resources),
    }
    await _apply(env, t_ref, TaskLifecycleState.WAITING_APPROVAL, extra_update={"wait": approval_wait})
    assert await _pump(env) >= 2
    page = await _inbox(env, task_id)
    assert [(note["type"], note["status"]) for note in page["notifications"]] == [("approval_required", "open")]
    approval_id = page["notifications"][0]["notification_id"]
    assert approval_id.startswith("approval:")
    assert page["notifications"][0]["category"] == "needs_attention"
    assert page["notifications"][0]["views"] == {
        "task": f"/api/tasks/{task_id}",
        "approval": f"/api/tasks/{task_id}/approval",
    }
    async with env["sessions"]() as db:
        await SqlApprovalLedger(db).decide(approval_ref, approved=True, reason="test decision", decided_by="operator")
        await db.commit()
    still_parked = await _inbox(env, task_id)
    assert still_parked["notifications"][0]["status"] == "resolved"

    # The approval resolves; the task parks on a TIMER wait: the resolved
    # approval stays honest, and the timer produces NO human to-do.
    await _apply(env, t_ref, TaskLifecycleState.RUNNING, extra_update={"wait": dict(_wait("resume"), consumed=True)})
    await _apply(env, t_ref, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("timer")})
    assert await _pump(env) >= 2
    page = await _inbox(env, task_id)
    assert [(note["type"], note["status"]) for note in page["notifications"]] == [("approval_required", "resolved")]

    # A human input wait is an open to-do; the old approval stays resolved.
    await _apply(env, t_ref, TaskLifecycleState.RUNNING, extra_update={"wait": dict(_wait("timer"), consumed=True)})
    await _apply(env, t_ref, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("provide_input")})
    assert await _pump(env) >= 2
    page = await _inbox(env, task_id)
    assert [(note["type"], note["status"]) for note in page["notifications"]] == [
        ("approval_required", "resolved"),
        ("input_required", "open"),
    ]
    input_id = page["notifications"][1]["notification_id"]
    assert input_id.startswith("input:")

    # The input resolves and the task succeeds: exactly ONE completion
    # notification although both task_state and run_terminal projected.
    # A past waiting_input event cannot be bound to its own wake kind, so
    # once the wait is no longer an open human to-do its notification
    # disappears (the completion is the resolution signal) — approvals keep
    # their resolved history instead.
    outcome = {
        "status": "succeeded",
        "content": "final answer body",
        "acceptance": _acceptance_record(task_id, r_ref.id, env["admin_ctx"].workspace_id, "final answer body"),
    }
    await _apply(
        env,
        t_ref,
        TaskLifecycleState.SUCCEEDED,
        extra_update={"wait": dict(_wait("provide_input"), consumed=True), "result": outcome},
        terminal_payload=outcome,
    )
    assert await _pump(env) >= 2
    page = await _inbox(env, task_id)
    notes = page["notifications"]
    assert [note["type"] for note in notes] == ["approval_required", "result_ready"]
    assert notes[0]["notification_id"] == approval_id and notes[0]["status"] == "resolved"
    assert input_id not in {note["notification_id"] for note in notes}
    completion = notes[1]
    assert completion["notification_id"] == f"completion:{task_id}"
    assert completion["category"] == "result"
    assert completion["run_id"] == str(r_ref.id)
    assert completion["views"]["run_detail"] == f"/api/runs/{r_ref.id}"

    # The authorized run detail behind the view entry carries the committed
    # delivery facts: bounded preview and the verified acceptance view.
    run_detail = (await env["client"].get(f"/api/runs/{r_ref.id}")).json()
    assert run_detail["projection"]["result_preview"] == "final answer body"
    assert run_detail["delivery_acceptance"]["status"] == "passed"

    # Re-delivery: relay replay and a from-scratch re-read re-derive the
    # same notification ids — a client merging by id never duplicates.
    assert await _pump(env) == 0  # projection idempotent
    again = await _inbox(env, task_id)
    assert [note["notification_id"] for note in again["notifications"]] == [note["notification_id"] for note in notes]

    # Failure, cancellation and reconciliation present separately.
    failed_id, failed_t, _ = await _bound_task(env, key="map-f")
    await _apply(env, failed_t, TaskLifecycleState.RUNNING)
    await _apply(env, failed_t, TaskLifecycleState.FAILED, terminal_payload={"status": "failed", "error": "boom"})
    await _pump(env)
    cancelled_id, cancelled_t, _ = await _bound_task(env, key="map-c")
    await _apply(env, cancelled_t, TaskLifecycleState.CANCELLED, terminal_payload={"status": "cancelled"})
    await _pump(env)
    recon_id, recon_t, _ = await _bound_task(env, key="map-r")
    await _apply(env, recon_t, TaskLifecycleState.RECONCILIATION_REQUIRED)
    await _pump(env)
    stopped = (await _inbox(env, failed_id))["notifications"]
    assert [(note["type"], note["category"]) for note in stopped] == [("task_stopped", "stop")]
    assert stopped[0]["notification_id"] == f"completion:{failed_id}"
    cancelled = (await _inbox(env, cancelled_id))["notifications"]
    assert [(note["type"], note["category"]) for note in cancelled] == [("task_stopped", "stop")]
    recon = (await _inbox(env, recon_id))["notifications"]
    assert [(note["type"], note["status"]) for note in recon] == [("reconciliation_required", "open")]

    # Converged reconciliation reports resolved, never a stale to-do; the
    # explicit convergence action also completes the task.
    await _apply(env, recon_t, TaskLifecycleState.FAILED, reconciled=True)
    await _pump(env)
    recon = (await _inbox(env, recon_id))["notifications"]
    assert [(note["type"], note.get("status", "stop")) for note in recon] == [
        ("reconciliation_required", "resolved"),
        ("task_stopped", "stop"),
    ]

    # An attempt-level terminal (task still running) is not a completion.
    attempt_id, attempt_t, attempt_r = await _bound_task(env, key="map-a")
    await _apply(env, attempt_t, TaskLifecycleState.RUNNING)
    async with env["sessions"]() as db:
        events = PlatformEventService(db)
        await events.emit(
            task_ref=attempt_t,
            run_ref=attempt_r,
            payload_schema_ref=RUN_TERMINAL,
            payload={"status": "failed", "error": "attempt crashed"},
            actor=ActorRef(kind=ActorKind.PLATFORM, id="task-control"),
            workspace_id=env["admin_ctx"].workspace_id,
        )
        await db.commit()
    assert (await _inbox(env, attempt_id))["notifications"] == []
    # When the task truly completes, the completion notification appears
    # once, keyed by the task.
    await _apply(env, attempt_t, TaskLifecycleState.SUCCEEDED, terminal_payload={"status": "succeeded"})
    await _pump(env)
    notes = (await _inbox(env, attempt_id))["notifications"]
    assert [note["notification_id"] for note in notes] == [f"completion:{attempt_id}"]
    assert notes[0]["type"] == "result_ready"

    # Leakage negatives: no wait token, no raw input, no tool body, no
    # acceptance internals ever leave through the inbox.
    dumped = json.dumps(await _inbox(env, task_id)) + json.dumps(await _inbox(env, failed_id))
    assert "wait-marker-1" not in dumped
    assert "secret-input-map-1" not in dumped
    assert "boom" not in dumped
    assert "declaration_sha256" not in dumped and "record_sha256" not in dumped


async def test_sequential_waits_supersede_and_agent_child_waits_stay_silent(env):
    """A superseded input wait never surfaces (a past waiting_input event
    cannot be bound to its own wake kind); an agent-child wake is a system
    continuation and never a human to-do."""
    task_id, t_ref, _r = await _bound_task(env, key="sup-1")
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    await _apply(
        env, t_ref, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("provide_input", marker="first")}
    )
    await _apply(
        env,
        t_ref,
        TaskLifecycleState.RUNNING,
        extra_update={"wait": dict(_wait("provide_input", marker="first"), consumed=True)},
    )
    await _apply(
        env, t_ref, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("provide_input", marker="second")}
    )
    assert await _pump(env) >= 3
    page = await _inbox(env, task_id)
    notes = page["notifications"]
    # Only the newest input wait is a to-do; the resolved first round never
    # appears as (or became) a misleading human item.
    assert [(note["type"], note["status"]) for note in notes] == [("input_required", "open")]
    assert "wait-marker-1" not in json.dumps(notes)

    # An agent-child wait is a system wake: no human notification at all.
    child_id, child_t, _ = await _bound_task(env, key="sup-2")
    await _apply(env, child_t, TaskLifecycleState.RUNNING)
    await _apply(env, child_t, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": _wait("agent_child")})
    await _pump(env)
    assert (await _inbox(env, child_id))["notifications"] == []

    expired_id, expired_t, _ = await _bound_task(env, key="expired-input")
    await _apply(env, expired_t, TaskLifecycleState.RUNNING)
    expired_wait = {**_wait("provide_input"), "wait_expires_at": "2000-01-01T00:00:00+00:00"}
    await _apply(env, expired_t, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": expired_wait})
    await _pump(env)
    assert (await _inbox(env, expired_id))["notifications"] == []


async def test_cursor_integrity_misuse_and_progress_preservation(env, monkeypatch):
    """Empty pages, pagination boundaries, non-notification skips, later
    backfills, foreign cursors and read failures preserving progress."""
    task_id, t_ref, _r = await _bound_task(env, key="cur-1")
    await _apply(env, t_ref, TaskLifecycleState.RUNNING)
    await _apply(env, t_ref, TaskLifecycleState.FAILED, terminal_payload={"status": "failed"})
    await _pump(env)

    # A task with no notification returns an honest empty page.
    empty_id, _t, _r2 = await _bound_task(env, key="cur-0")
    page = await _inbox(env, empty_id)
    assert page["notifications"] == [] and page["has_more"] is False and page["next_cursor"] is None

    # One notification per page: the cursor walks the stream, and the
    # skipped non-notification events never break resumption.
    first = await _inbox(env, task_id, limit=1)
    assert len(first["notifications"]) == 1 and first["has_more"] is True
    assert first["next_cursor"] is not None
    second = await _inbox(env, task_id, cursor=first["next_cursor"], limit=1)
    assert second["notifications"] == []
    assert second["next_cursor"] != first["next_cursor"]
    drained = await _inbox(env, task_id, cursor=second["next_cursor"])
    assert drained["notifications"] == [] and drained["has_more"] is False
    # Empty page == no more readable notifications now; the cursor stays
    # stable for continued polling.
    again = await _inbox(env, task_id, cursor=drained["next_cursor"])
    assert again["notifications"] == [] and again["next_cursor"] == drained["next_cursor"]

    # A later backfill after the saved cursor is picked up exactly once.
    backfill_id, backfill_t, _ = await _bound_task(env, key="cur-2")
    await _apply(env, backfill_t, TaskLifecycleState.RUNNING)
    await _pump(env)
    page = await _inbox(env, backfill_id)
    assert page["notifications"] == []
    saved_cursor = page["next_cursor"]
    await _apply(env, backfill_t, TaskLifecycleState.SUCCEEDED, terminal_payload={"status": "succeeded"})
    await _pump(env)
    resumed = await _inbox(env, backfill_id, cursor=saved_cursor)
    assert [note["type"] for note in resumed["notifications"]] == ["result_ready"]
    assert (await _inbox(env, backfill_id, cursor=resumed["next_cursor"]))["notifications"] == []

    # Misuse: malformed cursors and cursors bound to another Task/Run.
    for bad in ("junk", f"v1:{uuid.uuid4()}:0", f"v1:{task_id}:x", "v2:x:0", f"v1:{_r.id}:{10**40}"):
        response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox", params={"cursor": bad})
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_CURSOR"

    # A failing read advances nothing: the same cursor re-reads in full.
    original = env["store"].get_task_state
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("read model unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(env["store"], "get_task_state", flaky)
    response = await env["client"].get(f"/api/tasks/{task_id}/result-inbox", params={"cursor": first["next_cursor"]})
    monkeypatch.setattr(env["store"], "get_task_state", original)
    assert response.status_code == 500, response.text
    retried = await _inbox(env, task_id, cursor=first["next_cursor"], limit=1)
    assert retried == second

    monkeypatch.setattr("hecate.execution.task_result_inbox._SCAN_WINDOW", 1)
    skipped = await _inbox(env, task_id)
    assert skipped["notifications"] == [] and skipped["has_more"] is True
    assert skipped["next_cursor"] is not None
    following = await _inbox(env, task_id, cursor=skipped["next_cursor"])
    assert following["next_cursor"] != skipped["next_cursor"]


# ---------------------------------------------------------------------------
# Part B: real-TCP acceptance drill
# ---------------------------------------------------------------------------


class _InboxDrillPort(StubRuntimePort):
    """Deterministic controller port: a model barrier plus an input wait."""

    def __init__(self, barriers: Path) -> None:
        super().__init__()
        self._barriers = barriers
        self.hold_model = False
        self.request_input = False
        self.model_calls = 0
        self.business_calls: list[dict] = []

    async def _model_barrier(self) -> None:
        if not self.hold_model:
            return
        parked = self._barriers / "model.parked"
        parked.write_text("parked", encoding="utf-8")
        release = self._barriers / "model.release"
        deadline = time.monotonic() + 180
        while not release.is_file():
            if time.monotonic() >= deadline:
                raise TimeoutError("model barrier was never released")
            await asyncio.sleep(0.05)

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if not tool_messages:
            await self._model_barrier()
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": json.dumps({"sku": "S-9"})},
                    }
                ],
            }
            return
        yield {"content": json.dumps({"answer": "EU stock resolved"}), "tool_calls": None}

    async def tool_execute(self, name: str, args: dict, context=None):
        self.business_calls.append(dict(args))
        if self.request_input:
            return {
                "status": "input_required",
                "contract": {"fields": ["region"], "prompt": "Which region should I check?"},
            }
        return {"quantity": 3, "region": "EU"}


@pytest.mark.timeout(240)
async def test_rest_inbox_async_delivery_over_real_tcp(tmp_path, monkeypatch):
    """Submit over real TCP with the model blocked, disconnect, let the task
    finish, reconnect, resume from the saved cursor and read the authorized
    Run delivery facts — including an open→resolved input wait on a second
    task. Evidence and leakage negatives are recorded."""
    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    db_file = tmp_path / "inbox-drill.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})

    import sqlite3 as _sqlite3

    with _sqlite3.connect(db_file) as raw:
        raw.execute("PRAGMA journal_mode=WAL")
    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    viewer = uuid.uuid4()
    async with sessions() as db:
        db.add(OrganizationModel(id=ORG, name="inbox-org", slug="inbox-org", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="inbox-ws", slug="inbox-ws"))
        db.add(UserModel(id=OWNER, email="inbox-editor@example.com", hashed_password="unused"))
        db.add(UserModel(id=viewer, email="inbox-viewer@example.com", hashed_password="unused"))
        db.add(WorkspaceMemberModel(user_id=OWNER, workspace_id=WS, role=WorkspaceRole.EDITOR))
        db.add(WorkspaceMemberModel(user_id=viewer, workspace_id=WS, role=WorkspaceRole.VIEWER))
        agent = AgentModel(workspace_id=WS, name="inbox-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={
                "model": "stub",
                "tools": ["query_inventory"],
                "model_config": {"model": "stub-model"},
            },
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

    barriers = tmp_path / "barriers"
    barriers.mkdir()
    port = _InboxDrillPort(barriers)
    assembly = _ScriptedAssembly(port, guardrails=_no_guardrails)
    assembly.patch()
    monkeypatch.setitem(_BUILTIN_CLASSIFICATIONS, "query_inventory", SideEffectClass.READONLY)

    suite = DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core")
    monkeypatch.setattr(tasks_api, "get_durable_suite", lambda: suite)

    relay = OutboxRelay(store.session_factory, PlatformOutboxProjector(store, sessions), relay_key="platform")
    app = FastAPI()
    app.include_router(tasks_api.router, prefix="/api")
    app.include_router(tasks_api.runs_router, prefix="/api")
    app.include_router(tasks_api.commands_router, prefix="/api")

    async def request_db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = request_db
    state_handle = SimpleNamespace(durable_outbox_relay=relay)
    tasks_api.bind_worker_state(state_handle)
    server, serving, base_url = await serve_over_tcp(app)
    observations: list[dict] = []
    tokens = {
        "editor": create_access_token(OWNER, ORG, WS, WorkspaceRole.EDITOR.value),
        "viewer": create_access_token(viewer, ORG, WS, WorkspaceRole.VIEWER.value),
    }

    def _headers(actor="editor"):
        return {"Authorization": f"Bearer {tokens[actor]}"}

    async def _poll_until(predicate, label, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = await predicate()
            if value:
                return value
            await asyncio.sleep(0.1)
        raise AssertionError(f"timed out waiting for {label}")

    async def _task_state(task_id):
        record = await asyncio.to_thread(store.get_task_state, BackendRef(RefKind.TASK, "hecate", str(task_id)))
        return record.lifecycle_state.value if record else None

    evidence: dict = {"schema_version": 1, "database": "sqlite", "observations": observations}
    try:
        # --- Task 1: async submit, disconnect, completion, resume ----------
        port.hold_model = True
        async with AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:
            response = await client.post(
                "/api/tasks",
                json={
                    "goal": "async result delivery drill",
                    "agent_id": str(agent_id),
                    "input": {"messages": [{"role": "user", "content": "check the stock"}], "controller_loop": True},
                    "idempotency_key": "drill-task-1",
                    "result_delivery": "rest_inbox",
                },
                headers=_headers(),
            )
            assert response.status_code == 201, response.text
            refs = response.json()
            assert refs["result_delivery"] == "rest_inbox" and refs["replayed"] is False
            observations.append({"step": "submit-1", "http_status": response.status_code, "task": refs["task_id"]})
        # The references arrived while the deterministic model is parked on
        # its barrier — the submit is genuinely asynchronous.

        async def _model_parked():
            return (barriers / "model.parked").is_file()

        await _poll_until(_model_parked, "the model ask parked on the barrier", timeout=30)
        task_1 = uuid.UUID(refs["task_id"])
        run_1 = refs["run_id"]

        (barriers / "model.release").write_text("go", encoding="utf-8")

        async def _terminal_1():
            state = await _task_state(task_1)
            return state if state in ("succeeded", "failed", "reconciliation_required") else None

        final = await _poll_until(_terminal_1, "task 1 terminal")
        assert final == "succeeded", final

        # --- Reconnect: a brand-new client resumes from the start cursor --
        # Visibility advances through the normal worker projection, so the
        # reconnected client polls its cursor until the notification page
        # carries the completion.
        async with AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:

            async def _completion_page():
                page = await client.get(f"/api/tasks/{task_1}/result-inbox", headers=_headers())
                assert page.status_code == 200, page.text
                body = page.json()
                if any(note["type"] == "result_ready" for note in body["notifications"]):
                    return body
                return None

            body = await _poll_until(_completion_page, "the completion notification projected", timeout=60)
            notes = body["notifications"]
            assert [note["type"] for note in notes] == ["result_ready"], notes
            completion = notes[0]
            assert completion["notification_id"] == f"completion:{task_1}"
            assert completion["run_id"] == run_1 and completion["category"] == "result"
            cursor_1 = body["next_cursor"]
            observations.append({"step": "resume-1", "notifications": [n["notification_id"] for n in notes]})

            response = await client.get(completion["views"]["run_detail"], headers=_headers())
            assert response.status_code == 200, response.text
            run = response.json()
            assert run["projection"]["state"] == "succeeded"
            assert "EU stock resolved" in run["projection"]["result_preview"]
            observations.append({"step": "run-detail-1", "projection_state": run["projection"]["state"]})

            # Re-reading the start cursor repeats the stable notification.
            # Resuming from the processed position cannot emit it again.
            again = (await client.get(f"/api/tasks/{task_1}/result-inbox", headers=_headers())).json()
            assert [n["notification_id"] for n in again["notifications"]] == [n["notification_id"] for n in notes]
            replayed = (
                await client.get(f"/api/tasks/{task_1}/result-inbox", headers=_headers(), params={"cursor": cursor_1})
            ).json()
            assert replayed["has_more"] is False
            assert replayed["notifications"] == []
            settled = (
                await client.get(
                    f"/api/tasks/{task_1}/result-inbox", headers=_headers(), params={"cursor": replayed["next_cursor"]}
                )
            ).json()
            assert settled["notifications"] == [] and settled["has_more"] is False

            # A viewer who is not the submitter has no inbox here.
            foreign = await client.get(f"/api/tasks/{task_1}/result-inbox", headers=_headers("viewer"))
            assert foreign.status_code == 404, foreign.text

        # --- Task 2: an open input wait, wake, resume from the cursor -----
        port.request_input = True
        async with AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:
            response = await client.post(
                "/api/tasks",
                json={
                    "goal": "needs-attention drill",
                    "agent_id": str(agent_id),
                    "input": {"messages": [{"role": "user", "content": "check the stock"}], "controller_loop": True},
                    "idempotency_key": "drill-task-2",
                    "result_delivery": "rest_inbox",
                },
                headers=_headers(),
            )
            assert response.status_code == 201, response.text
            refs_2 = response.json()
            task_2 = uuid.UUID(refs_2["task_id"])
            observations.append({"step": "submit-2", "task": refs_2["task_id"]})

            async def _parked_wait():
                record = await asyncio.to_thread(store.get_task_state, BackendRef(RefKind.TASK, "hecate", str(task_2)))
                wait = (record.extra or {}).get("wait") if record else None
                if record is not None and record.lifecycle_state.value == "waiting_input" and isinstance(wait, dict):
                    return wait
                assert record is None or record.lifecycle_state.value not in ("failed",), record.extra
                return None

            wait = await _poll_until(_parked_wait, "task 2 parked on the human input wait")
            t_ref_2 = BackendRef(RefKind.TASK, "hecate", str(task_2))
            revision = (await asyncio.to_thread(store.get_task_state, t_ref_2)).revision

            async def _open_input_page():
                page = await client.get(f"/api/tasks/{task_2}/result-inbox", headers=_headers())
                assert page.status_code == 200, page.text
                body = page.json()
                if any(n["type"] == "input_required" and n["status"] == "open" for n in body["notifications"]):
                    return body
                return None

            page = await _poll_until(_open_input_page, "the open input notification projected", timeout=60)
            notes = page["notifications"]
            assert [(n["type"], n["status"]) for n in notes] == [("input_required", "open")], notes
            input_note_id = notes[0]["notification_id"]
            saved_cursor = page["next_cursor"]
            observations.append({"step": "inbox-open-wait", "notification": input_note_id})

            # Resolve the wait over REST: the provide_input wake, revision
            # and token facts are all server-owned.
            wake = await client.post(
                f"/api/tasks/{task_2}/commands",
                json={
                    "kind": "provide_input",
                    "expected_revision": revision,
                    "payload": {"region": "EU"},
                    "payload_schema_ref": "urn:hecate:task-user-input/0",
                    "detail_ns": {"wait_token": wait["wait_token"]},
                },
                headers=_headers(),
            )
            assert wake.status_code in (200, 201), wake.text

        async def _terminal_2():
            state = await _task_state(task_2)
            return state if state in ("succeeded", "failed", "reconciliation_required") else None

        assert await _poll_until(_terminal_2, "task 2 terminal") == "succeeded"

        async with AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:

            async def _resumed_page():
                page = await client.get(
                    f"/api/tasks/{task_2}/result-inbox", headers=_headers(), params={"cursor": saved_cursor}
                )
                assert page.status_code == 200, page.text
                body = page.json()
                if any(n["type"] == "result_ready" for n in body["notifications"]):
                    return body
                return None

            # Resume from the processed cursor: the new fact since then is
            # the completion — one notification, keyed by the task.
            resumed = await _poll_until(_resumed_page, "the resumed completion projected", timeout=60)
            notes = resumed["notifications"]
            assert [n["type"] for n in notes] == ["result_ready"], notes
            assert notes[0]["notification_id"] == f"completion:{task_2}"
            run_2 = notes[0]["run_id"]

            async def _run_2_delivered():
                response = await client.get(f"/api/runs/{run_2}", headers=_headers())
                assert response.status_code == 200, response.text
                run_body = response.json()
                if (run_body.get("projection") or {}).get("state") == "succeeded":
                    return run_body
                return None

            run = await _poll_until(_run_2_delivered, "the run 2 delivery projection", timeout=60)
            # A full re-read (the documented to-do refresh) never resurrects
            # the closed input wait and never duplicates the completion.
            full = (await client.get(f"/api/tasks/{task_2}/result-inbox", headers=_headers())).json()
            assert [n["notification_id"] for n in full["notifications"]] == [f"completion:{task_2}"]
            observations.append(
                {
                    "step": "resume-2",
                    "notifications": [n["notification_id"] for n in notes],
                    "run_state": run["projection"]["state"],
                }
            )
            async with sessions() as db:
                member = await db.scalar(
                    select(WorkspaceMemberModel).where(
                        WorkspaceMemberModel.user_id == OWNER, WorkspaceMemberModel.workspace_id == WS
                    )
                )
                assert member is not None
                member.deleted = True
                await db.commit()
            revoked = await client.get(
                f"/api/tasks/{task_2}/result-inbox", headers=_headers(), params={"cursor": saved_cursor}
            )
            assert revoked.status_code == 401, revoked.text
            observations.append({"step": "revoked-cursor-replay", "http_status": revoked.status_code})
    finally:
        await stop_tcp_server(server, serving)
        tasks_api.unbind_worker_state(state_handle)
        app.dependency_overrides.clear()
        assembly.unpatch()
        await engine.dispose()
        store.dispose()

        evidence["code_version"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO, text=True).strip()
        evidence["code_dirty"] = subprocess.call(["git", "diff", "--quiet", "HEAD"], cwd=_REPO) != 0
        evidence["source_sha256"] = {
            name: hashlib.sha256((_REPO / name).read_bytes()).hexdigest()
            for name in (
                "tests/test_api/test_task_result_inbox.py",
                "src/hecate/execution/task_result_inbox.py",
                "src/hecate/execution/task_control.py",
                "src/hecate/channel/api/tasks.py",
            )
        }
        directory = Path(os.environ.get("HECATE_INBOX_EVIDENCE_DIR", str(tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task-result-inbox-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Part C: trusted-field REST-inbox closed loop over real TCP
# ---------------------------------------------------------------------------


class _TrustedFieldDrillProvider(tspec.ScriptedTrustedFieldProvider):
    """Hold the first model call to verify asynchronous submission."""

    def __init__(self, case: str, *, hold_at_start: bool = False) -> None:
        super().__init__(tspec.CASE_SCRIPTS[case][1])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not hold_at_start:
            self.release.set()

    async def complete(self, *, messages, tools):
        self.started.set()
        await self.release.wait()
        return await super().complete(messages=messages, tools=tools)


@asynccontextmanager
async def _trusted_inbox_stack(tmp_path, monkeypatch, case: str, *, hold_at_start: bool = False):
    """Serve the production Controller and REST routes with synthetic model and tools."""
    from hecate_runtime.eventstore import InMemoryEventStore

    provider = _TrustedFieldDrillProvider(case, hold_at_start=hold_at_start)
    gateway = platform_driver._RecordingGateway(provider)
    registry = tdriver.TrustedFieldRegistry(case, model_rounds=gateway.rounds)
    patch = platform_driver._SeamPatch(
        registry, gateway, InMemoryEventStore(), tool_definitions=tspec.tool_definitions()
    )
    submitter = platform_driver.OWNER
    viewer = uuid.uuid4()
    bystander = uuid.uuid4()
    workspace = platform_driver.WS
    previous_tasks = set(_background_tasks)
    async with AsyncExitStack() as stack:
        platform = platform_driver.PlatformEnvironment(tmp_path, "trusted-inbox")
        stack.push_async_callback(platform.close)
        agent_id = await platform._seed(
            with_approval_rule=False,
            persona=tspec.SYSTEM_PROMPT,
            config_extra={"field_provenance": tspec.PROFILE_DECLARATION},
        )
        sessions = platform.session_factory
        async with sessions() as db:
            db.add(UserModel(id=viewer, email="trusted-viewer@example.com", hashed_password="unused"))
            db.add(UserModel(id=bystander, email="trusted-bystander@example.com", hashed_password="unused"))
            db.add(WorkspaceMemberModel(user_id=submitter, workspace_id=workspace, role=WorkspaceRole.EDITOR))
            db.add(WorkspaceMemberModel(user_id=viewer, workspace_id=workspace, role=WorkspaceRole.VIEWER))
            db.add(WorkspaceMemberModel(user_id=bystander, workspace_id=workspace, role=WorkspaceRole.EDITOR))
            await db.commit()

        patch.patch()
        stack.callback(patch.unpatch)
        patches = stack.enter_context(monkeypatch.context())
        patches.setattr(tasks_api, "get_durable_suite", lambda: platform.suite)
        relay = OutboxRelay(
            platform.store.session_factory, PlatformOutboxProjector(platform.store, sessions), relay_key="platform"
        )
        app = FastAPI()
        app.include_router(tasks_api.router, prefix="/api")
        app.include_router(tasks_api.runs_router, prefix="/api")
        app.include_router(tasks_api.commands_router, prefix="/api")

        async def request_db():
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db] = request_db
        state_handle = SimpleNamespace(durable_outbox_relay=relay)
        tasks_api.bind_worker_state(state_handle)
        stack.callback(tasks_api.unbind_worker_state, state_handle)

        async def drain_dispatches():
            stragglers = await platform_driver._cancel_case_tasks(previous_tasks)
            assert stragglers == 0, f"{stragglers} trusted inbox dispatches survived cleanup"

        stack.push_async_callback(drain_dispatches)
        server, serving, base_url = await serve_over_tcp(app)
        stack.push_async_callback(stop_tcp_server, server, serving)
        yield {
            "base_url": base_url,
            "sessions": sessions,
            "store": platform.store,
            "agent_id": agent_id,
            "submitter": submitter,
            "registry": registry,
            "provider": provider,
            "tokens": {
                "submitter": create_access_token(submitter, platform_driver.ORG, workspace, WorkspaceRole.EDITOR.value),
                "viewer": create_access_token(viewer, platform_driver.ORG, workspace, WorkspaceRole.VIEWER.value),
                "bystander": create_access_token(bystander, platform_driver.ORG, workspace, WorkspaceRole.EDITOR.value),
            },
        }


def _write_trusted_inbox_evidence(tmp_path, scenario: str, **observations) -> None:
    directory = Path(os.environ.get("HECATE_INBOX_EVIDENCE_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": 1,
        "scenario": scenario,
        "database": "sqlite",
        "observations": observations,
        "code_version": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO, text=True).strip(),
        "code_dirty": subprocess.call(["git", "diff", "--quiet", "HEAD"], cwd=_REPO) != 0,
        "source_sha256": {
            name: hashlib.sha256((_REPO / name).read_bytes()).hexdigest()
            for name in (
                "tests/test_api/test_task_result_inbox.py",
                "src/hecate/execution/task_result_inbox.py",
                "src/hecate/execution/field_provenance.py",
                "tests/scenarios/baseline/controller_host_trusted_field_spec.py",
            )
        },
    }
    output = directory / f"trusted-field-inbox-{scenario}-{uuid.uuid4().hex}.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)


def _trusted_headers(env, actor="submitter"):
    return {"Authorization": f"Bearer {env['tokens'][actor]}"}


async def _poll_until(predicate, label, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {label}")


async def _submit_trusted_task(env, key: str) -> dict:
    """One asynchronous rest_inbox submission over real TCP (JWT submitter)."""
    async with AsyncClient(base_url=env["base_url"], timeout=15, trust_env=False) as client:
        response = await client.post(
            "/api/tasks",
            json={
                "goal": f"trusted-field inbox drill {key}",
                "agent_id": str(env["agent_id"]),
                "input": {
                    "messages": [{"role": "user", "content": tspec.USER_PROMPT}],
                    "controller_loop": True,
                    "controller": {"max_turns": 8},
                    "output_schema": tspec.OUTPUT_SCHEMA,
                },
                "idempotency_key": key,
                "result_delivery": "rest_inbox",
            },
            headers=_trusted_headers(env),
        )
        assert response.status_code == 201, response.text
        return response.json()


async def _trusted_state(env, task_id):
    record = await asyncio.to_thread(env["store"].get_task_state, BackendRef(RefKind.TASK, "hecate", str(task_id)))
    return record


async def _trusted_terminal(env, task_id):
    record = await _trusted_state(env, task_id)
    if record is not None and record.lifecycle_state.value in ("succeeded", "failed", "reconciliation_required"):
        return record.lifecycle_state.value
    return None


async def _trusted_parked_wait(env, task_id):
    record = await _trusted_state(env, task_id)
    wait = (record.extra or {}).get("wait") if record else None
    if record is not None and record.lifecycle_state.value == "waiting_input" and isinstance(wait, dict):
        return wait
    assert record is None or record.lifecycle_state.value != "failed", record.extra
    return None


async def _trusted_run_actions(env, run_id: str) -> list[dict]:
    """Read the action ledger for the Run returned at submission."""
    return await asyncio.to_thread(env["store"].list_run_actions, BackendRef(RefKind.RUN, "hecate", run_id))


def _trusted_open_input_page(client, env, task_id):
    async def _page():
        response = await client.get(f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env))
        assert response.status_code == 200, response.text
        body = response.json()
        if any(n["type"] == "input_required" and n["status"] == "open" for n in body["notifications"]):
            return body
        return None

    return _page


def _trusted_completion_page(client, env, task_id):
    async def _page():
        response = await client.get(f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env))
        assert response.status_code == 200, response.text
        body = response.json()
        if any(n["type"] == "result_ready" for n in body["notifications"]):
            return body
        return None

    return _page


def _trusted_completion_from_cursor(client, env, task_id, cursor):
    async def _page():
        response = await client.get(
            f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env), params={"cursor": cursor}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if any(n["type"] == "result_ready" for n in body["notifications"]):
            return body
        return None

    return _page


def _trusted_wake_body(detail: dict, command_id: str, value: str) -> dict:
    """The provide_input command body: revision and token are server-owned."""
    return {
        "kind": "provide_input",
        "command_id": command_id,
        "expected_revision": detail["revision"],
        "payload": {tspec.PROTECTED_FIELD: value},
        "payload_schema_ref": TASK_USER_INPUT_SCHEMA_REF,
        "detail_ns": {"wait_token": detail["wait"]["wait_token"]},
    }


def _assert_trusted_inbox_metadata(*pages: dict, wait_token: str | None = None) -> None:
    """Check the public inbox envelope and the two notification shapes."""
    for page in pages:
        assert set(page) == {"task_id", "result_delivery", "notifications", "next_cursor", "has_more"}
        for note in page["notifications"]:
            status_field = "status" if note["type"] == "input_required" else "state"
            assert set(note) == {
                "notification_id",
                "type",
                "category",
                "task_id",
                "run_id",
                "occurred_at",
                "views",
                status_field,
            }
    payload = json.dumps(pages)
    for secret in (*tspec.DEFAULT_VALUES.values(), tspec.RECEIPT_ID, wait_token):
        if secret is not None:
            assert secret not in payload
    assert "wait_token" not in payload and tspec.PROTECTED_FIELD not in payload
    assert "Reorder submitted" not in payload


@pytest.mark.timeout(240)
async def test_trusted_receipt_delivery_over_real_tcp(tmp_path, monkeypatch):
    """The model sources ``order_ref`` from the same resource's read receipt;
    the business write lands exactly once; after a disconnect the reconnecting
    submitter reads the original Run's ``result_ready`` notification and the
    delivery-accepted deliverable behind its authorized view link."""
    async with _trusted_inbox_stack(tmp_path, monkeypatch, "receipt_sourced", hold_at_start=True) as env:
        refs = await _submit_trusted_task(env, "trusted-receipt-1")
        assert refs["result_delivery"] == "rest_inbox" and refs["replayed"] is False
        task_id = uuid.UUID(refs["task_id"])
        run_id = refs["run_id"]
        await asyncio.wait_for(env["provider"].started.wait(), timeout=30)
        env["provider"].release.set()
        assert await _poll_until(lambda: _trusted_terminal(env, task_id), "task terminal", timeout=120) == "succeeded"

        # Side-effect evidence: the receipt value landed exactly once.
        writes = [call for call in env["registry"].calls if call["tool"] == tspec.WRITE_TOOL]
        reads = [call for call in env["registry"].calls if call["tool"] == tspec.READ_TOOL]
        assert len(writes) == 1 and len(reads) == 1
        (read_receipt,) = [receipt for receipt in env["registry"].receipts if receipt["tool"] == tspec.READ_TOOL]
        write_args = writes[0]["arguments"]
        assert {key: write_args[key] for key in tspec.RESOURCE} == reads[0]["arguments"] == tspec.RESOURCE
        assert write_args[tspec.PROTECTED_FIELD] == read_receipt["content"][tspec.PROTECTED_FIELD]
        assert env["registry"].side_effects == 1
        assert [v["verdict"] for v in tdriver.guard_verdicts(await _trusted_run_actions(env, run_id))] == ["executed"]

        # Reconnect: a brand-new client resumes from the start cursor.
        async with AsyncClient(base_url=env["base_url"], timeout=15, trust_env=False) as client:
            page = await _poll_until(
                _trusted_completion_page(client, env, task_id), "the completion notification projected", timeout=60
            )
            (completion,) = page["notifications"]
            assert completion["notification_id"] == f"completion:{task_id}"
            assert completion["task_id"] == str(task_id) and completion["run_id"] == run_id
            assert completion["category"] == "result"
            assert completion["views"] == {"task": f"/api/tasks/{task_id}", "run_detail": f"/api/runs/{run_id}"}

            # The authorized Run detail behind the view entry carries the
            # committed delivery facts: bounded preview and the verified
            # acceptance view.
            detail = await client.get(completion["views"]["run_detail"], headers=_trusted_headers(env))
            assert detail.status_code == 200, detail.text
            run = detail.json()
            assert run["projection"]["state"] == "succeeded"
            preview = run["projection"]["result_preview"]
            answer = json.loads(preview)
            assert tspec.RECEIPT_ID in answer["answer"]
            acceptance = run["delivery_acceptance"]
            assert acceptance["status"] == "passed"
            assert acceptance["content_sha256"] == hashlib.sha256(preview.encode("utf-8")).hexdigest()

            # The public action-ledger summary records the sourced write.
            actions = (await client.get(f"/api/runs/{run_id}/actions", headers=_trusted_headers(env))).json()
            write_rows = [a for a in actions["actions"] if a["tool_name"] == tspec.WRITE_TOOL]
            assert len(write_rows) == 1 and write_rows[0]["outcome"] == "succeeded"

            # Stable re-delivery by id; resuming cannot emit the completion again.
            again = (await client.get(f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env))).json()
            assert [n["notification_id"] for n in again["notifications"]] == [completion["notification_id"]]
            settled = (
                await client.get(
                    f"/api/tasks/{task_id}/result-inbox",
                    headers=_trusted_headers(env),
                    params={"cursor": page["next_cursor"]},
                )
            ).json()
            assert settled["notifications"] == [] and settled["has_more"] is False

            _assert_trusted_inbox_metadata(page, again, settled)
    _write_trusted_inbox_evidence(
        tmp_path,
        "receipt-sourced",
        task_id=str(task_id),
        run_id=run_id,
        completion_id=completion["notification_id"],
        business_writes=len(writes),
        acceptance=acceptance["status"],
    )


@pytest.mark.timeout(240)
async def test_trusted_guess_blocked_then_provided_input_completes_over_real_tcp(tmp_path, monkeypatch):
    """A guessed ``order_ref`` is intercepted with zero business writes; the
    inbox to-do carries a stable id and authorized links; one idempotent
    ``provide_input`` command wakes the SAME Task/Run; the resumed cursor
    reads the unique completion and a full re-read drops the old to-do."""
    async with _trusted_inbox_stack(tmp_path, monkeypatch, "guessed_value") as env:
        refs = await _submit_trusted_task(env, "trusted-guess-1")
        task_id = uuid.UUID(refs["task_id"])
        t_ref = BackendRef(RefKind.TASK, "hecate", str(task_id))
        run_id = refs["run_id"]
        run_ref = BackendRef(RefKind.RUN, "hecate", run_id)

        await _poll_until(lambda: _trusted_parked_wait(env, task_id), "task parked on the human input wait", timeout=90)
        # The real guard intercepted the guess before the business call: no
        # registry side effect, and the blocked write's ledger outcome is the
        # profile's own input request.
        assert env["registry"].side_effects == 0
        assert [call["tool"] for call in env["registry"].calls] == [tspec.READ_TOOL]
        actions = await _trusted_run_actions(env, run_id)
        assert [v["verdict"] for v in tdriver.guard_verdicts(actions)] == ["input_required"]
        (blocked,) = [action for action in actions if action["intent"]["action_name"] == tspec.WRITE_TOOL]
        guessed_args = {
            **tspec.RESOURCE,
            "quantity": tspec.read_content("guessed_value")["quantity"],
            tspec.PROTECTED_FIELD: tspec.DEFAULT_VALUES["guessed"],
        }
        assert blocked["intent"]["arguments_digest"] == tool_arguments_digest(guessed_args)

        async with AsyncClient(base_url=env["base_url"], timeout=15, trust_env=False) as client:
            page = await _poll_until(
                _trusted_open_input_page(client, env, task_id), "the open input notification projected", timeout=60
            )
            (note,) = page["notifications"]
            assert note["notification_id"].startswith("input:")
            assert note["category"] == "needs_attention"
            assert note["task_id"] == str(task_id) and note["run_id"] == run_id
            assert note["views"] == {"task": f"/api/tasks/{task_id}", "commands": f"/api/tasks/{task_id}/commands"}
            saved_cursor = page["next_cursor"]
            assert saved_cursor

            # Stable identity: a re-read repeats the same notice, never a
            # second to-do.
            stable = (await client.get(f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env))).json()
            assert [n["notification_id"] for n in stable["notifications"]] == [note["notification_id"]]

            # The authorized EDITOR wakes over REST with the server-owned
            # revision and wait token read from the task detail.
            detail = await client.get(note["views"]["task"], headers=_trusted_headers(env))
            assert detail.status_code == 200, detail.text
            body = detail.json()
            assert type(body["revision"]) is int
            assert body["wait"]["contract_ref"]["contract"]["fields"] == [tspec.PROTECTED_FIELD]
            wake_body = _trusted_wake_body(body, "trusted-provide-order-ref", tspec.DEFAULT_VALUES["provided"])
            first = await client.post(note["views"]["commands"], json=wake_body, headers=_trusted_headers(env))
            assert first.status_code == 201, first.text
            assert first.json()["state"] == "applied", first.text

            # The same command_id and identical request replays the settled
            # receipt: the input is applied exactly once.
            replay = await client.post(f"/api/tasks/{task_id}/commands", json=wake_body, headers=_trusted_headers(env))
            assert replay.status_code == 201, replay.text
            assert replay.json()["command_id"] == "trusted-provide-order-ref"
            assert replay.json()["state"] == "applied"
            assert "idempotent replay" in replay.json()["detail"]
            receipts = (await client.get(f"/api/tasks/{task_id}/commands", headers=_trusted_headers(env))).json()
            assert receipts["total"] == 1
            assert receipts["items"][0]["command_id"] == "trusted-provide-order-ref"
            assert receipts["items"][0]["state"] == "applied"

        # Exactly one input revision carries the authorized value.
        (revision,) = await asyncio.to_thread(env["store"].list_input_revisions, t_ref, run_ref)
        assert revision["content"] == wake_body["payload"]

        # The SAME Task/Run continues with the authorized value: exactly one
        # business write, the guessed value never landed.
        assert await _poll_until(lambda: _trusted_terminal(env, task_id), "task terminal", timeout=120) == "succeeded"
        writes = [call for call in env["registry"].calls if call["tool"] == tspec.WRITE_TOOL]
        assert len(writes) == 1 and writes[0]["arguments"][tspec.PROTECTED_FIELD] == tspec.DEFAULT_VALUES["provided"]
        assert env["registry"].side_effects == 1
        assert [v["verdict"] for v in tdriver.guard_verdicts(await _trusted_run_actions(env, run_id))] == [
            "input_required",
            "executed",
        ]

        # Reconnect: resume from the processed cursor — the only new fact is
        # the completion; the old to-do is gone from a full re-read too.
        async with AsyncClient(base_url=env["base_url"], timeout=15, trust_env=False) as client:
            resumed = await _poll_until(
                _trusted_completion_from_cursor(client, env, task_id, saved_cursor),
                "the resumed completion projected",
                timeout=60,
            )
            (completion,) = resumed["notifications"]
            assert completion["type"] == "result_ready"
            assert completion["notification_id"] == f"completion:{task_id}"
            assert completion["task_id"] == str(task_id) and completion["run_id"] == run_id
            assert completion["views"] == {"task": note["views"]["task"], "run_detail": f"/api/runs/{run_id}"}
            full = (await client.get(f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env))).json()
            assert full["notifications"] == [completion]

            run_detail = await client.get(completion["views"]["run_detail"], headers=_trusted_headers(env))
            assert run_detail.status_code == 200, run_detail.text
            run = run_detail.json()
            preview = run["projection"]["result_preview"]
            assert tspec.RECEIPT_ID in json.loads(preview)["answer"]
            acceptance = run["delivery_acceptance"]
            assert acceptance["status"] == "passed"
            assert acceptance["content_sha256"] == hashlib.sha256(preview.encode("utf-8")).hexdigest()

            task_detail = await client.get(note["views"]["task"], headers=_trusted_headers(env))
            assert task_detail.status_code == 200, task_detail.text
            assert [row["run_id"] for row in task_detail.json()["runs"]] == [run_id]
            _assert_trusted_inbox_metadata(page, stable, resumed, full, wait_token=body["wait"]["wait_token"])
    _write_trusted_inbox_evidence(
        tmp_path,
        "guessed-value",
        task_id=str(task_id),
        run_id=run_id,
        input_required_id=note["notification_id"],
        completion_id=completion["notification_id"],
        input_revisions=1,
        business_writes=len(writes),
        acceptance=acceptance["status"],
    )


@pytest.mark.timeout(240)
async def test_trusted_inbox_authorization_boundaries_over_real_tcp(tmp_path, monkeypatch):
    """The inbox stays submitter-scoped: a same-workspace non-submitter is
    one 404, a VIEWER cannot supply the trusted field, a revoked submitter
    cannot resume the saved cursor, and the pages expose only notifications
    and authorized view links."""
    async with _trusted_inbox_stack(tmp_path, monkeypatch, "guessed_value") as env:
        refs = await _submit_trusted_task(env, "trusted-boundary-1")
        task_id = uuid.UUID(refs["task_id"])
        await _poll_until(lambda: _trusted_parked_wait(env, task_id), "task parked on the human input wait", timeout=90)

        async with AsyncClient(base_url=env["base_url"], timeout=15, trust_env=False) as client:
            page = await _poll_until(
                _trusted_open_input_page(client, env, task_id), "the open input notification projected", timeout=60
            )
            (note,) = page["notifications"]
            saved_cursor = page["next_cursor"]

            # Same workspace, different identity: one indistinguishable 404 —
            # the inbox is submitter-scoped even for a same-workspace EDITOR,
            # and a viewer who is not the submitter has no inbox either.
            stranger = await client.get(
                f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env, "bystander")
            )
            assert stranger.status_code == 404, stranger.text
            assert stranger.json()["detail"]["error"]["code"] == "NOT_FOUND"
            viewer_page = await client.get(
                f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env, "viewer")
            )
            assert viewer_page.status_code == 404, viewer_page.text

            # A VIEWER cannot supply the trusted field: the write gate runs
            # before any command storage, and the task state never moves.
            detail = await client.get(f"/api/tasks/{task_id}", headers=_trusted_headers(env))
            assert detail.status_code == 200, detail.text
            body = detail.json()
            viewer_wake = await client.post(
                f"/api/tasks/{task_id}/commands",
                json=_trusted_wake_body(body, "viewer-provide-order-ref", "PO-VIEWER-0001"),
                headers=_trusted_headers(env, "viewer"),
            )
            assert viewer_wake.status_code == 403, viewer_wake.text
            assert viewer_wake.json()["detail"]["error"]["code"] == "FORBIDDEN"
            receipts = (await client.get(f"/api/tasks/{task_id}/commands", headers=_trusted_headers(env))).json()
            assert receipts["total"] == 0
            still = (await client.get(f"/api/tasks/{task_id}", headers=_trusted_headers(env))).json()
            assert still["lifecycle_state"] == "waiting_input" and still["revision"] == body["revision"]

            # The authorized EDITOR (here the submitter) wakes the task.
            wake = await client.post(
                f"/api/tasks/{task_id}/commands",
                json=_trusted_wake_body(body, "trusted-boundary-wake", tspec.DEFAULT_VALUES["provided"]),
                headers=_trusted_headers(env),
            )
            assert wake.status_code == 201, wake.text
            assert wake.json()["state"] == "applied", wake.text

        assert await _poll_until(lambda: _trusted_terminal(env, task_id), "task terminal", timeout=120) == "succeeded"

        async with AsyncClient(base_url=env["base_url"], timeout=15, trust_env=False) as client:
            resumed = await _poll_until(
                _trusted_completion_from_cursor(client, env, task_id, saved_cursor),
                "the resumed completion projected",
                timeout=60,
            )
            assert [n["type"] for n in resumed["notifications"]] == ["result_ready"]
            full = (await client.get(f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env))).json()
            assert [n["notification_id"] for n in full["notifications"]] == [f"completion:{task_id}"]

            # The linked control view resolves for its audience.
            task_view = await client.get(note["views"]["task"], headers=_trusted_headers(env))
            assert task_view.status_code == 200, task_view.text

            # Revoked submitter: the old cursor cannot continue reading.
            async with env["sessions"]() as db:
                member = await db.scalar(
                    select(WorkspaceMemberModel).where(
                        WorkspaceMemberModel.user_id == env["submitter"],
                        WorkspaceMemberModel.workspace_id == platform_driver.WS,
                    )
                )
                assert member is not None
                member.deleted = True
                await db.commit()
            revoked = await client.get(
                f"/api/tasks/{task_id}/result-inbox", headers=_trusted_headers(env), params={"cursor": saved_cursor}
            )
            assert revoked.status_code == 401, revoked.text
            _assert_trusted_inbox_metadata(page, resumed, full, wait_token=body["wait"]["wait_token"])
    _write_trusted_inbox_evidence(
        tmp_path,
        "authorization",
        task_id=str(task_id),
        run_id=refs["run_id"],
        http_status={
            "stranger": stranger.status_code,
            "viewer": viewer_wake.status_code,
            "revoked": revoked.status_code,
        },
    )
