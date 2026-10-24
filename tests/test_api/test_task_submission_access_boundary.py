"""Task submission write access boundary (step14).

Covers the write gate on ``POST /api/tasks``: an active workspace first
(403 ``NO_WORKSPACE``, unchanged — workspace-less and system-scope
identities never gain a global submission path), then the server-side
EDITOR floor. The gate precedes business validation, the idempotent
lookup/replay, registration and dispatch, so a downgraded caller cannot
replay an existing idempotency key and every rejected request is
side-effect free. Authorized submissions keep their semantics verbatim:
201 new, 200 replay, 409 conflict, and the ``wait`` view still runs the
real dispatch boundary (stubbed here at the entry executor only).
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from hecate_durable.storage.models import Base as DurableBase
from hecate_durable.worker import DurableWorker
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from hecate.channel.api import tasks as tasks_api
from hecate.channel.api.tasks import get_task_control_service, router
from hecate.contracts.execution.durable import CommandState, ControlCommandKind, ControlCommandRecord
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution import task_control as task_control_module
from hecate.execution.governance_events import TASK_SUBMITTED, PlatformEventService
from hecate.execution.task_control import TaskControlService
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.platform_event import PlatformEventModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.conftest import DEFAULT_WORKSPACE_ID
from tests.test_execution.conftest import harness as harness


def _ctx_for(ws_id: uuid.UUID, org_id: uuid.UUID, role: WorkspaceRole | None, user_id: uuid.UUID) -> AuthContext:
    """A workspace-scoped JWT identity with the given membership role."""
    return AuthContext(
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


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


class _CountingRelay:
    """Relay double that records status/pump calls without projecting."""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def status(self) -> dict[str, Any]:
        """Record operational status access."""
        self._calls.append("relay.status")
        return {"relay_key": "test", "last_event_row_id": 0, "lag": 0, "skipped_event_ids": []}

    async def pump_once(self) -> int:
        """Record an attempted projection without consuming the outbox."""
        self._calls.append("relay.pump")
        return 0


@pytest.fixture
async def env(harness, auth_context, monkeypatch):
    """Reuse the shared dual-engine integration harness and swap only identity.

    Submission registration queries the durable table through the application
    session, so both engines must share a database. The existing execution
    fixture owns schema setup and cleanup; this module creates no engines.
    """
    session_factory = harness["session_factory"]
    store = harness["store"]

    async with session_factory() as db:
        db.add(UserModel(id=auth_context.user_id, email="submission-owner@example.com", hashed_password="test-only"))
        db.add(
            OrganizationModel(
                id=auth_context.org_id,
                name="org",
                slug=f"submission-org-{auth_context.org_id.hex}",
                owner_id=auth_context.user_id,
            )
        )
        db.add(
            WorkspaceModel(
                id=DEFAULT_WORKSPACE_ID,
                org_id=auth_context.org_id,
                name="ws",
                slug=f"ws-{DEFAULT_WORKSPACE_ID.hex}",
            )
        )
        agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="submission-boundary")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub"},
            content_hash="a" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                organization_id=auth_context.org_id,
                owner_user_id=auth_context.user_id,
            )
        )
        db.add(
            AgentDeploymentModel(
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
        )
        await db.commit()

    calls: list[str] = []
    service_inputs: list[dict[str, Any]] = []
    original_submit = TaskControlService.submit

    async def observed_submit(self, **kwargs):
        calls.append("service.submit")
        service_inputs.append(deepcopy(kwargs))
        return await original_submit(self, **kwargs)

    monkeypatch.setattr(TaskControlService, "submit", observed_submit)
    for name in ("submit_task", "apply_task_state", "record", "transition"):
        original = getattr(store, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observed)

    # Execution boundary replacement: the real dispatcher runs, only the
    # entry executor answers with a deterministic reply.
    entry_calls: list[dict] = []

    async def _execute(self, db, context, **kwargs):
        entry_calls.append({**kwargs, "stream": context.stream})
        return {"status": "succeeded", "content": "boundary stub reply"}

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", _execute)

    spawned: list[str] = []

    def capture_spawn(coro):
        spawned.append("dispatch")
        coro.close()

    monkeypatch.setattr(task_control_module, "_spawn_background", capture_spawn)

    dispatcher = PlatformTaskDispatcher(store, session_factory)
    monkeypatch.setattr(
        tasks_api,
        "_worker_state",
        SimpleNamespace(
            durable_worker=DurableWorker(store, dispatcher, leases=store.leases),
            durable_outbox_relay=_CountingRelay(calls),
        ),
    )
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
    holder: dict[str, AuthContext] = {"ctx": auth_context}
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def swapped_auth():
        return holder["ctx"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    assert get_task_control_service not in app.dependency_overrides
    verify_session = session_factory()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "db": verify_session,
                "session_factory": session_factory,
                "store": store,
                "agent_id": agent.id,
                "calls": calls,
                "service_inputs": service_inputs,
                "entry": entry_calls,
                "spawned": spawned,
                "holder": holder,
                "admin_ctx": auth_context,
            }
    finally:
        app.dependency_overrides.clear()
        await verify_session.close()


@pytest.fixture
async def second_workspace(env):
    """A second workspace with a viewer member and its own agent."""
    async with env["session_factory"]() as db:
        user_id = uuid.uuid4()
        db.add(UserModel(id=user_id, email=f"foreign-{user_id.hex}@example.com", hashed_password="test-only"))
        org = OrganizationModel(name="Org foreign", slug=f"org-foreign-{uuid.uuid4().hex[:8]}", owner_id=user_id)
        db.add(org)
        await db.flush()
        ws = WorkspaceModel(org_id=org.id, name="ws-foreign", slug=f"ws-foreign-{uuid.uuid4().hex[:8]}")
        db.add(ws)
        await db.flush()
        db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=WorkspaceRole.VIEWER))
        agent = AgentModel(workspace_id=ws.id, name="foreign-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub"},
            content_hash="b" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=ws.id,
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
        return ws, agent.id, _ctx_for(ws.id, org.id, WorkspaceRole.VIEWER, user_id)


def _as(env: dict[str, Any], ctx: AuthContext) -> None:
    env["holder"]["ctx"] = ctx


def _body(env: dict[str, Any], **overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "goal": "summarize the inventory",
        "agent_id": str(env["agent_id"]),
        "input": {"messages": [{"role": "user", "content": "summarize"}]},
    }
    payload.update(overrides)
    return payload


async def _submit(env: dict[str, Any], body: dict[str, Any]) -> Any:
    return await env["client"].post("/api/tasks", json=body)


async def _persistent_snapshot(env: dict[str, Any]) -> dict[str, Any]:
    snapshot = {}
    for model in (TaskModel, RunModel, PlatformEventModel):
        table = model.__table__
        rows = await env["db"].execute(select(table).order_by(*table.primary_key.columns))
        snapshot[table.name] = deepcopy([dict(row) for row in rows.mappings()])
    with env["store"].engine.connect() as conn:
        for table in DurableBase.metadata.sorted_tables:
            rows = conn.execute(select(table).order_by(*table.primary_key.columns))
            snapshot[table.name] = deepcopy([dict(row) for row in rows.mappings()])
    return snapshot


# ---------------------------------------------------------------------------
# Authorized submissions: EDITOR and ADMIN, both submission views
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("stream", [False, True])
async def test_authorized_submission_preserves_modes_and_binding(env, role, wait, stream):
    """Both roles preserve request modes, persisted identity and dispatch semantics."""
    ctx = replace(env["admin_ctx"], role=role)
    _as(env, ctx)
    response = await _submit(env, _body(env, wait=wait, stream=stream))
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"task_id", "run_id", "issuer_domain", "lifecycle_state", "replayed", "result", "reason"}
    assert body["replayed"] is False and body["issuer_domain"] == "hecate"
    assert env["service_inputs"][-1]["wait"] is wait
    assert env["service_inputs"][-1]["stream"] is stream
    assert env["service_inputs"][-1]["user_id"] == ctx.user_id
    if wait:
        assert body["lifecycle_state"] == "succeeded"
        assert body["result"]["status"] == "succeeded"
        # Response freshness has a separate strict regression below; this
        # matrix checks authorization, mode propagation and persisted facts.
        assert isinstance(body["result"]["content"], str)
        assert len(env["entry"]) == 1 and env["entry"][0]["stream"] is stream
        assert env["spawned"] == []
    else:
        assert body["lifecycle_state"] == "queued" and body["result"] is None
        assert env["spawned"] == ["dispatch"] and env["entry"] == []
    async with env["session_factory"]() as db:
        task = await db.get(TaskModel, uuid.UUID(body["task_id"]))
        run = await db.get(RunModel, uuid.UUID(body["run_id"]))
        assert task is not None and run is not None
        assert task.workspace_id == run.workspace_id == ctx.workspace_id
        assert run.task_id == task.id
        assert run.identity_chain["initiator"] == str(ctx.user_id)
        if wait:
            assert run.projection["result_preview"] == "boundary stub reply"
    payload = env["store"].get_task_input(BackendRef(RefKind.TASK, "hecate", body["task_id"]))
    assert payload["platform_run_id"] == body["run_id"]
    assert payload["workspace_id"] == str(ctx.workspace_id)
    assert payload["user_id"] == str(ctx.user_id)
    assert payload["agent_id"] == str(env["agent_id"])
    assert payload["stream"] is stream


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Confirmed on base 92534a2e: the request identity map retains the pre-worker Run projection; "
        "response freshness is outside the REST authorization change"
    ),
)
async def test_wait_result_reads_the_completed_worker_projection(env):
    """Keep the independently reproduced response-freshness defect visible."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    response = await _submit(env, _body(env, wait=True))
    if response.status_code != 201:
        pytest.fail(f"unexpected submission failure: {response.text}")
    async with env["session_factory"]() as db:
        run = await db.get(RunModel, uuid.UUID(response.json()["run_id"]))
        if run is None or run.projection.get("result_preview") != "boundary stub reply":
            pytest.fail("worker did not persist the expected result")
    assert response.json()["result"]["content"] == "boundary stub reply"


async def test_replay_and_conflict_semantics_survive_the_gate(env):
    """201 new, 200 replay with the original association, 409 on conflict."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    first = await _submit(env, _body(env, idempotency_key="submission-key-1"))
    assert first.status_code == 201, first.text
    replay = await _submit(env, _body(env, idempotency_key="submission-key-1"))
    assert replay.status_code == 200, replay.text
    assert replay.json()["task_id"] == first.json()["task_id"]
    assert replay.json()["replayed"] is True
    conflict = await _submit(env, _body(env, idempotency_key="submission-key-1", goal="different goal"))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["error"]["code"] == "IDEMPOTENCY_CONFLICT"


# ---------------------------------------------------------------------------
# Role floor: viewer, role-less, workspace-less and system identities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_below_editor_is_forbidden_before_any_service_work(env, role):
    """Structurally valid bodies never reach the submission service."""
    _as(env, replace(env["admin_ctx"], role=role))
    calls_before, entry_before, spawned_before = list(env["calls"]), list(env["entry"]), list(env["spawned"])
    response = await _submit(env, _body(env, wait=True))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert env["calls"] == calls_before
    assert env["entry"] == entry_before
    assert env["spawned"] == spawned_before


async def test_forged_roles_do_not_escalate(env):
    """Body- and query-supplied roles are ignored by the gate."""
    _as(env, replace(env["admin_ctx"], role=None))
    body = _body(env, role="admin")
    as_body = await _submit(env, body)
    as_query = await env["client"].post("/api/tasks?role=admin", json=_body(env))
    assert as_body.status_code == as_query.status_code == 403
    assert as_body.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert env["calls"] == [] and env["entry"] == []


async def test_invalid_business_target_still_denied_below_editor(env, second_workspace):
    """A low-role caller is rejected even when the target is invalid."""
    _, foreign_agent, _ = second_workspace
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.VIEWER))
    foreign = await _submit(env, _body(env, agent_id=str(foreign_agent)))
    whitespace = await _submit(env, _body(env, goal="   "))
    assert foreign.status_code == whitespace.status_code == 403
    assert env["calls"] == [] and env["entry"] == []


async def test_missing_workspace_identity_keeps_existing_rejection(env):
    """No workspace: 403 NO_WORKSPACE, exactly as before the gate."""
    ghost = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )
    _as(env, ghost)
    response = await _submit(env, _body(env))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"


async def test_system_scope_keeps_existing_rejection(env):
    """The bootstrap key gains no submission capability."""
    _as(env, _system_ctx())
    response = await _submit(env, _body(env))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    assert env["calls"] == [] and env["entry"] == []


# ---------------------------------------------------------------------------
# Downgrade cannot replay; restore can
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wait", [False, True])
async def test_downgraded_user_cannot_replay_idempotent_key(env, wait):
    """The gate precedes the idempotent lookup; the receipt stays intact."""
    editor = replace(env["admin_ctx"], role=WorkspaceRole.EDITOR)
    _as(env, editor)
    body = _body(env, idempotency_key="downgrade-key", wait=wait)
    first = await _submit(env, body)
    assert first.status_code == 201, first.text
    original = first.json()
    entry_seeded, spawned_seeded = list(env["entry"]), list(env["spawned"])
    calls_seeded = len(env["calls"])
    snapshot_seeded = await _persistent_snapshot(env)
    assert snapshot_seeded["tasks"] and snapshot_seeded["runs"]
    assert snapshot_seeded["durable_task_state"]

    _as(env, replace(editor, role=WorkspaceRole.VIEWER))
    replay = await _submit(env, body)
    assert replay.status_code == 403, replay.text
    assert replay.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert "task_id" not in replay.text and "run_id" not in replay.text
    assert env["calls"][calls_seeded:] == []
    assert await _persistent_snapshot(env) == snapshot_seeded

    _as(env, editor)
    restored = await _submit(env, body)
    assert restored.status_code == 200, restored.text
    assert restored.json()["task_id"] == original["task_id"]
    assert restored.json()["replayed"] is True
    assert restored.json()["run_id"] == original["run_id"]
    assert env["entry"] == entry_seeded and env["spawned"] == spawned_seeded
    assert await _persistent_snapshot(env) == snapshot_seeded


# ---------------------------------------------------------------------------
# Side-effect freedom of every rejection on populated facts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["viewer", "no_role", "no_workspace", "system"],
)
async def test_rejected_requests_leave_all_state_untouched(env, case):
    """Denials never register, submit, dispatch or alter populated facts."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    seeded = await _submit(env, _body(env, idempotency_key=f"seeded-{case}"))
    assert seeded.status_code == 201, seeded.text
    seeded_body = seeded.json()
    task_ref = BackendRef(RefKind.TASK, "hecate", seeded_body["task_id"])
    run_ref = BackendRef(RefKind.RUN, "hecate", seeded_body["run_id"])
    env["store"].record(
        ControlCommandRecord(
            command_id=f"existing-{case}",
            kind=ControlCommandKind.CANCEL,
            issuer="existing-issuer",
            task_ref=task_ref,
            run_ref=run_ref,
            state=CommandState.REQUESTED,
            issued_at="2000-01-01T00:00:00+00:00",
            expires_at="2000-01-02T00:00:00+00:00",
            extra={"workspace_id": str(DEFAULT_WORKSPACE_ID)},
        )
    )
    await PlatformEventService(env["db"]).emit(
        task_ref=task_ref,
        run_ref=run_ref,
        payload_schema_ref=TASK_SUBMITTED,
        payload={"goal": "existing fact"},
        actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    await env["db"].commit()
    ctx = replace(env["admin_ctx"], role=WorkspaceRole.VIEWER)
    if case == "no_role":
        ctx = replace(ctx, role=None)
    elif case == "no_workspace":
        ctx = replace(ctx, workspace_id=None, role=None)
    elif case == "system":
        ctx = _system_ctx()
    _as(env, ctx)
    before = await _persistent_snapshot(env)
    for table in (
        "tasks",
        "runs",
        "platform_events",
        "durable_task_state",
        "durable_submission",
        "durable_command",
        "durable_event_log",
    ):
        assert before[table], table
    calls_before, entry_before, spawned_before = list(env["calls"]), list(env["entry"]), list(env["spawned"])
    response = await _submit(env, _body(env, idempotency_key=f"rejected-{case}"))
    assert response.status_code == 403, response.text
    assert await _persistent_snapshot(env) == before
    assert env["calls"] == calls_before
    assert env["entry"] == entry_before
    assert env["spawned"] == spawned_before


# ---------------------------------------------------------------------------
# Authorized business validation stays with the service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["foreign", "missing"])
async def test_authorized_invisible_agent_is_rejected_by_service(env, second_workspace, case):
    """EDITOR keeps the existing agent-ownership rules."""
    _, foreign_agent, _ = second_workspace
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    target_id = foreign_agent if case == "foreign" else uuid.uuid4()
    before = await _persistent_snapshot(env)
    response = await _submit(env, _body(env, agent_id=str(target_id)))
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert env["calls"] == ["service.submit"]
    assert env["entry"] == [] and env["spawned"] == []
    assert await _persistent_snapshot(env) == before


async def test_authorized_soft_deleted_and_undeployed_agents_rejected(env):
    """Soft-deleted agents and missing default deployments stay service errors."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    agent = await env["db"].get(AgentModel, env["agent_id"])
    agent.deleted = True
    await env["db"].commit()
    deleted = await _submit(env, _body(env))
    assert deleted.status_code == 404, deleted.text

    agent.deleted = False
    fresh = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="undeployed-agent")
    env["db"].add(fresh)
    await env["db"].commit()
    undeployed = await _submit(env, _body(env, agent_id=str(fresh.id)))
    assert undeployed.status_code == 422, undeployed.text
    assert undeployed.json()["detail"]["error"]["code"] == "INVALID_REQUEST"


async def test_structural_validation_stays_422_before_the_gate(env):
    """FastAPI request-shape errors keep their existing 422 surface."""
    _as(env, replace(env["admin_ctx"], role=None))
    missing_goal = await env["client"].post(
        "/api/tasks",
        json={"agent_id": str(env["agent_id"]), "input": {"messages": [{"role": "user", "content": "x"}]}},
    )
    wrong_input_type = await _submit(env, _body(env, input="not-a-dict"))
    assert missing_goal.status_code == wrong_input_type.status_code == 422
    assert env["calls"] == []


async def test_authorized_empty_messages_stay_a_service_error(env):
    """EDITOR reaches the service, whose business validation still applies."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    response = await _submit(env, _body(env, input={"messages": []}))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
