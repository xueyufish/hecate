"""Run observation read access boundary (step14).

Covers the two-level read gate on the four run observation entries
(detail, actions, event pagination, SSE stream): visibility first —
foreign, missing, soft-deleted runs and runs whose parent task was
deleted all surface as the same 404 echoing only the caller-supplied
run_id — then a server-side VIEWER floor. The SSE entry completes the
gate before the stream is created, and rejected requests never trigger
the outbox relay projection, ledger reads or state changes.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from hecate_runtime.eventstore import InMemoryEventStore
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from hecate.channel.api import tasks as tasks_api
from hecate.channel.api.tasks import router, runs_router
from hecate.contracts.execution.durable import (
    ActionIntent,
    ActionOutcome,
    ActionOutcomeRecord,
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyKey,
)
from hecate.contracts.execution.events import ActorKind, ActorRef, EventKind
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.governance_events import RUN_TERMINAL, TASK_SUBMITTED, PlatformEventService
from hecate.execution.task_control import TaskControlError, TaskControlNotFoundError, TaskControlService
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.platform_event import PlatformEventModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceRole
from tests.conftest import DEFAULT_WORKSPACE_ID


def _system_ctx() -> AuthContext:
    """Deploy-time bootstrap identity (workspace-less by construction)."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


@pytest.fixture
async def env(db_session, auth_context, tmp_path, monkeypatch):
    """W1 task + run under an SQL suite, swappable auth, counting relay."""
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="run-observe-boundary")
    db_session.add(agent)
    await db_session.flush()
    version = AgentVersionModel(
        agent_id=agent.id,
        version=1,
        config_snapshot={"model": "stub"},
        content_hash="a" * 64,
    )
    db_session.add(version)
    await db_session.flush()
    principal = AgentPrincipalModel(
        id=agent.id,
        agent_id=agent.id,
        workspace_id=DEFAULT_WORKSPACE_ID,
        organization_id=auth_context.org_id,
        owner_user_id=auth_context.user_id,
    )
    deployment = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=DEFAULT_WORKSPACE_ID,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        issuer_domain="hecate",
        capability_snapshot={},
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        is_default=True,
    )
    db_session.add_all([principal, deployment])
    await db_session.flush()
    chain = IdentityChain(
        initiator=str(auth_context.user_id),
        principal_id=str(principal.id),
        workload=WorkloadIdentity(
            deployment=BackendRef(RefKind.DEPLOYMENT, "hecate", str(deployment.id)),
            workload_id="hecate:run-observe",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="boundary run",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=DEFAULT_WORKSPACE_ID,
        deployment_id=deployment.id,
        identity_chain=chain,
        backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
    )
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'run-observe-boundary.db'}", source="platform")
    store.create_schema()
    monkeypatch.setattr(
        "hecate.channel.api.tasks.get_durable_suite",
        lambda: DurableSuite(
            store=store,
            recorder=store,
            ledger=store,
            backend="postgres",
            ledger_source="core",
        ),
    )
    calls: list[str] = []

    class StubCountingRelay:
        """Observe projection attempts without changing the seeded facts."""

        async def pump_once(self) -> None:
            """Record a relay projection attempt."""
            calls.append("relay")

    relay = StubCountingRelay()
    monkeypatch.setattr(tasks_api, "_worker_state", SimpleNamespace(durable_outbox_relay=relay))
    for name in ("get", "record", "transition", "list_run_actions", "get_task_state"):
        original = getattr(store, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observed)
    for cls, name in (
        (PlatformEventService, "read_run_events"),
        (PlatformEventService, "latest_run_envelope"),
        (RunEventMapper, "read_events"),
        (TaskControlService, "_pump_events"),
        (TaskControlService, "_dispatch_task"),
    ):
        original = getattr(cls, name)

        async def observed_async(self, *args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return await _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, observed_async)
    holder: dict[str, AuthContext] = {"ctx": auth_context}
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(runs_router, prefix="/api")

    async def shared_db():
        yield db_session

    async def swapped_auth():
        return holder["ctx"]

    app.dependency_overrides[get_db] = shared_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            records = {
                "client": client,
                "db": db_session,
                "store": store,
                "task": task,
                "run": run,
                "registry": registry,
                "chain": chain.to_dict(),
                "holder": holder,
                "admin_ctx": auth_context,
                "calls": calls,
                "relay": relay,
            }
            await _seed_facts(records)
            calls.clear()
            yield records
    finally:
        app.dependency_overrides.clear()
        store.dispose()


ENTRIES = ("", "/actions", "/events", "/events/stream")
INVISIBLE = ("foreign_identity", "foreign_run", "missing", "deleted_run", "deleted_task", "foreign_task")


async def _seed_facts(records):
    store, task, run = records["store"], records["task"], records["run"]
    t_ref = BackendRef(RefKind.TASK, task.issuer_domain, str(task.id))
    r_ref = BackendRef(RefKind.RUN, run.issuer_domain, str(run.id))
    store.submit_task(
        key=IdempotencyKey(
            "existing-submission", str(records["admin_ctx"].user_id), str(DEFAULT_WORKSPACE_ID), "a" * 64
        ),
        task_ref=t_ref,
        run_ref=r_ref,
        input_payload={"goal": "existing input"},
    )
    store.record_intent_ex(
        ActionIntent("existing-action", "lookup", "arguments-digest", ToolSideEffectClass.READONLY),
        task_ref=t_ref,
        run_ref=r_ref,
        execution_id="existing-execution",
        tool_call_id="existing-call",
        session_id="sensitive-session",
    )
    claim, token = store.claim_ex("existing-action", holder="test-holder")
    assert claim.claimed
    store.record_outcome_ex(
        ActionOutcomeRecord("existing-action", ActionOutcome.SUCCEEDED, result_digest="result-digest"),
        claim_token=token,
        result_payload={"secret": "sensitive-result"},
    )
    store.record(
        ControlCommandRecord(
            command_id="existing-command",
            kind=ControlCommandKind.CANCEL,
            issuer=str(records["admin_ctx"].user_id),
            task_ref=t_ref,
            run_ref=r_ref,
            issued_at="2000-01-01T00:00:00+00:00",
            expires_at="2000-01-02T00:00:00+00:00",
            state=CommandState.REQUESTED,
            extra={"workspace_id": str(DEFAULT_WORKSPACE_ID)},
        )
    )
    events = PlatformEventService(records["db"])
    for schema, payload in ((TASK_SUBMITTED, {"goal": "existing input"}), (RUN_TERMINAL, {"status": "succeeded"})):
        await events.emit(
            task_ref=t_ref,
            run_ref=r_ref,
            payload_schema_ref=schema,
            payload=payload,
            actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
            workspace_id=DEFAULT_WORKSPACE_ID,
        )
    await records["db"].commit()


async def _snapshot(records):
    result = {}
    for model in (TaskModel, RunModel, PlatformEventModel):
        table = model.__table__
        rows = await records["db"].execute(select(table).order_by(*table.primary_key.columns))
        result[table.name] = deepcopy([dict(row) for row in rows.mappings()])
    with records["store"].engine.connect() as conn:
        for table in DurableBase.metadata.sorted_tables:
            rows = conn.execute(select(table).order_by(*table.primary_key.columns))
            result[table.name] = deepcopy([dict(row) for row in rows.mappings()])
    return result


@pytest.fixture
async def second_workspace(db_session, auth_context):
    """A real second workspace with a server-resolved identity."""
    org = OrganizationModel(name="Other org", slug=f"other-{uuid.uuid4().hex[:8]}", owner_id=auth_context.user_id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name="Other workspace", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    await db_session.commit()
    return ws


async def _deny_case(records, other_workspace, case):
    run, task = records["run"], records["task"]
    run_id = run.id
    ctx = replace(records["admin_ctx"], role=None)
    if case == "foreign_identity":
        ctx = replace(ctx, org_id=other_workspace.org_id, workspace_id=other_workspace.id)
    elif case == "foreign_run":
        run.workspace_id = other_workspace.id
    elif case == "missing":
        run_id = uuid.uuid4()
    elif case == "deleted_run":
        run.deleted = True
    elif case == "deleted_task":
        task.deleted = True
    elif case == "foreign_task":
        task.workspace_id = other_workspace.id
    elif case == "no_workspace":
        ctx = replace(ctx, workspace_id=None)
    elif case == "system":
        ctx = _system_ctx()
    records["holder"]["ctx"] = ctx
    await records["db"].commit()
    return run_id


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("case", (*INVISIBLE, "no_role", "no_workspace", "system"))
async def test_denial_precedes_every_read_and_leaves_facts_unchanged(env, second_workspace, case, entry):
    """Four routes reject before reading stores or creating an SSE response."""
    run_id = await _deny_case(env, second_workspace, case)
    before = await _snapshot(env)
    assert before["tasks"] and before["runs"] and before["platform_events"]
    assert before["durable_event_log"] and before["durable_command"]
    assert before["durable_action_intent"] and before["durable_action_outcome"]
    response = await env["client"].get(f"/api/runs/{run_id}{entry}?role=admin")
    if case in INVISIBLE:
        assert response.status_code == 404, response.text
        assert response.json() == {
            "detail": {
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"run {run_id} not found in workspace",
                    "details": None,
                }
            }
        }
    else:
        assert response.status_code == 403, response.text
        expected = "NO_WORKSPACE" if case in {"no_workspace", "system"} else "FORBIDDEN"
        assert response.json()["detail"]["error"]["code"] == expected
    assert "text/event-stream" not in response.headers.get("content-type", "")
    assert "event: error" not in response.text and "data: " not in response.text
    assert env["calls"] == []
    assert await _snapshot(env) == before


def _stream_events(response):
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_authorized_roles_preserve_real_content_and_cursor_resume(env, role):
    """Successful JSON and SSE reads preserve facts, whitelist, and cursors."""
    before = await _snapshot(env)
    url = f"/api/runs/{env['run'].id}"
    expected = {}
    for suffix in ENTRIES:
        response = await env["client"].get(url + suffix)
        assert response.status_code == 200
        expected[suffix] = response.text if suffix.endswith("stream") else response.json()
    env["holder"]["ctx"] = replace(env["admin_ctx"], role=role)
    for suffix in ENTRIES:
        response = await env["client"].get(url + suffix)
        assert response.status_code == 200, response.text
        assert (response.text if suffix.endswith("stream") else response.json()) == expected[suffix]
    action = expected["/actions"]["actions"][0]
    assert action["action_key"] == "existing-action" and action["outcome"] == "succeeded"
    assert set(action) == {
        "action_key",
        "execution_id",
        "tool_call_id",
        "tool_name",
        "arguments_digest",
        "side_effect_class",
        "state",
        "outcome",
        "result_digest",
        "pending_reconciliation",
    }
    assert "sensitive-result" not in json.dumps(expected) and "sensitive-session" not in json.dumps(expected)
    page = await env["client"].get(url + "/events", params={"limit": 1})
    assert page.status_code == 200
    first = page.json()
    assert first["has_more"] is True and len(first["events"]) == 1
    tail = await env["client"].get(url + "/events", params={"cursor": first["next_cursor"], "limit": 1})
    assert tail.status_code == 200
    assert tail.json()["events"][0]["payload_schema_ref"] == RUN_TERMINAL
    resumed = await env["client"].get(url + "/events/stream", params={"cursor": first["next_cursor"]})
    assert resumed.status_code == 200 and resumed.headers["content-type"].startswith("text/event-stream")
    frames = _stream_events(resumed)
    assert [frame["event_id"] for frame in frames] == [tail.json()["events"][0]["event_id"]]
    ended = await env["client"].get(url + "/events/stream", params={"cursor": tail.json()["next_cursor"]})
    assert ended.status_code == 200 and ended.text == ""
    assert "_pump_events" in env["calls"] and "relay" in env["calls"]
    assert await _snapshot(env) == before


@pytest.mark.parametrize("entry_path", [False, True])
@pytest.mark.parametrize("method", ["page", "stream"])
@pytest.mark.parametrize("case", INVISIBLE)
async def test_service_visibility_precedes_relay_and_event_reads(env, second_workspace, case, method, entry_path):
    """Internal callers also reject invisible resources before any projection."""
    if entry_path:
        env["run"].projection = {"entry_name": "chat"}
    run_id = await _deny_case(env, second_workspace, case)
    workspace_id = env["holder"]["ctx"].workspace_id
    service = TaskControlService(env["db"], store=env["store"], recorder=env["store"], relay=env["relay"])
    before = await _snapshot(env)
    with pytest.raises(TaskControlNotFoundError, match=f"run {run_id} not found in workspace"):
        if method == "page":
            await service.read_run_events(workspace_id, run_id)
        else:
            stream = service.stream_run_events(workspace_id, run_id)
            try:
                await anext(stream)
            finally:
                await stream.aclose()
    assert env["calls"] == []
    assert await _snapshot(env) == before


async def test_entry_store_events_preserve_safe_payload_and_stream_resume(env, monkeypatch):
    """The entry-backed event branch keeps the same cursor and safe payload."""
    event_store = InMemoryEventStore()
    monkeypatch.setattr("hecate.core.composition.entry_assembly.get_shared_event_store", lambda: event_store)
    run, task = env["run"], env["task"]
    run.projection = {"entry_name": "chat", "state": "succeeded"}
    await env["db"].commit()
    mapper = RunEventMapper(
        BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
        BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
        event_store=event_store,
    )
    first = await mapper.map_stream_event({"type": "message", "content": "visible reply", "secret": "private-backend"})
    terminal = await mapper.write_terminal_envelope(state="succeeded", extra={"secret": "private-terminal"})
    env["holder"]["ctx"] = replace(env["admin_ctx"], role=WorkspaceRole.VIEWER)
    env["calls"].clear()
    url = f"/api/runs/{run.id}"
    page = await env["client"].get(url + "/events", params={"limit": 1})
    assert page.status_code == 200
    assert page.json()["events"][0]["event_id"] == first.event_id
    assert page.json()["events"][0].get("extra", {}) == {}
    assert "private-backend" not in page.text
    response = await env["client"].get(url + "/events/stream", params={"cursor": page.json()["next_cursor"]})
    assert response.status_code == 200
    frames = _stream_events(response)
    assert len(frames) == 1 and frames[0]["event_id"] == terminal.event_id
    assert frames[0]["kind"] == EventKind.TERMINAL.value and frames[0].get("extra", {}) == {}
    assert "private-terminal" not in response.text
    assert "read_events" in env["calls"] and "relay" in env["calls"]


async def test_established_stream_keeps_runtime_error_frame(env, monkeypatch):
    """A post-admission failure keeps the existing SSE error-frame contract."""

    async def broken_stream(self, workspace_id, run_id, **kwargs):
        if run_id:
            raise TaskControlError("private storage diagnostic")
        yield

    monkeypatch.setattr(TaskControlService, "stream_run_events", broken_stream)
    env["holder"]["ctx"] = replace(env["admin_ctx"], role=WorkspaceRole.VIEWER)
    response = await env["client"].get(f"/api/runs/{env['run'].id}/events/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: error\ndata: {"code": 500}' in response.text
    assert "private storage diagnostic" not in response.text
