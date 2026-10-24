"""Control-observation read access boundary (step14).

Covers the read gate on the three control-observation REST entries
(``GET /api/tasks/{id}/commands``, ``GET /api/commands/{command_id}`` and
``GET /api/reconciliation/pending``): an active workspace first (403
``NO_WORKSPACE``, unchanged — workspace-less and system-scope identities
never gain a global query), then resource visibility where a resource is
addressed (unified 404), then the server-side VIEWER floor. The gate
precedes command listing and state changes (the single-command visibility
check may perform a pure recorder lookup), the single-read lazy expiry and the
reconciliation expiry sweep, so every rejected request is side-effect
free; authorized reads keep the recorded-state semantics verbatim.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from hecate.channel.api import tasks as tasks_api
from hecate.channel.api.tasks import commands_router, get_task_control_service, reconciliation_router, router
from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyKey,
    TaskLifecycleState,
)
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.governance_events import TASK_SUBMITTED, PlatformEventService
from hecate.execution.task_control import TaskControlService
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
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.conftest import DEFAULT_WORKSPACE_ID

COMMAND_WHITELIST = {
    "command_id",
    "kind",
    "state",
    "issued_at",
    "issuer",
    "task_ref",
    "run_ref",
    "expires_at",
    "expected_revision",
}


def _ctx_for(ws: WorkspaceModel, role: WorkspaceRole | None) -> AuthContext:
    """A workspace-scoped JWT identity with the given membership role."""
    user_id = uuid.uuid4()
    return AuthContext(
        user_id=user_id,
        org_id=ws.org_id,
        workspace_id=ws.id,
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


@pytest.fixture
async def env(db_session, auth_context, tmp_path, monkeypatch):
    """W1 task under an SQL suite with swappable auth and observed reads."""
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="observation-boundary")
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
            workload_id="hecate:observation-boundary",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="observation boundary task",
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
    await PlatformEventService(db_session).emit(
        task_ref=BackendRef(RefKind.TASK, "hecate", str(task.id)),
        run_ref=BackendRef(RefKind.RUN, "hecate", str(run.id)),
        payload_schema_ref=TASK_SUBMITTED,
        payload={"goal": task.goal},
        actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    await db_session.commit()
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'observation-boundary.db'}", source="platform")
    store.create_schema()
    calls: list[str] = []

    class StubCountingRelay:
        """Detect status reads and projection attempts at the real service seam."""

        def status(self) -> dict[str, Any]:
            """Return deterministic operational status and record the read."""
            calls.append("relay.status")
            return {"relay_key": "test", "last_event_row_id": 0, "lag": 0, "skipped_event_ids": []}

        async def pump_once(self) -> None:
            """Record an attempted outbox projection."""
            calls.append("relay.pump")

    monkeypatch.setattr(tasks_api, "_worker_state", SimpleNamespace(durable_outbox_relay=StubCountingRelay()))
    for name in (
        "get",
        "record",
        "transition",
        "list_task_commands",
        "list_pending_commands",
        "list_tasks",
        "get_task_state",
        "list_run_actions",
    ):
        original = getattr(store, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observed)

    async def dispatch_observed(self, task_ref):
        calls.append("dispatch")

    monkeypatch.setattr(TaskControlService, "_dispatch_task", dispatch_observed)
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
    app.include_router(commands_router, prefix="/api")
    app.include_router(reconciliation_router, prefix="/api")

    async def shared_db():
        yield db_session

    async def swapped_auth():
        return holder["ctx"]

    app.dependency_overrides[get_db] = shared_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    assert get_task_control_service not in app.dependency_overrides
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "db": db_session,
                "store": store,
                "task": task,
                "run": run,
                "calls": calls,
                "holder": holder,
                "admin_ctx": auth_context,
            }
    finally:
        app.dependency_overrides.clear()
        store.dispose()


@pytest.fixture
async def second_workspace(db_session):
    """A second organization + workspace with a viewer member."""
    user_id = uuid.uuid4()
    org = OrganizationModel(name="Org foreign", slug=f"org-foreign-{uuid.uuid4().hex[:8]}", owner_id=user_id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name="ws-foreign", slug=f"ws-foreign-{uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=WorkspaceRole.VIEWER))
    await db_session.commit()
    return ws, replace(_ctx_for(ws, WorkspaceRole.VIEWER), user_id=user_id)


def _as(env: dict[str, Any], ctx: AuthContext) -> None:
    env["holder"]["ctx"] = ctx


def _seed_command(
    env: dict[str, Any],
    command_id: str,
    *,
    workspace_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    state: CommandState = CommandState.REQUESTED,
) -> None:
    now = datetime.now(UTC)
    env["store"].record(
        ControlCommandRecord(
            command_id=command_id,
            kind=ControlCommandKind.CANCEL,
            issuer="user-1",
            task_ref=BackendRef(RefKind.TASK, "hecate", str(env["task"].id)),
            run_ref=BackendRef(RefKind.RUN, "hecate", str(env["run"].id)),
            payload={"private": "secret-command-payload"},
            payload_schema_ref="test.payload.v1",
            detail_ns={"private": "secret-command-detail"},
            issued_at=now.isoformat(),
            expires_at=expires_at.isoformat() if expires_at else None,
            state=state,
            extra={
                "workspace_id": str(DEFAULT_WORKSPACE_ID if workspace_id is None else workspace_id),
                "private": "secret-command-extra",
            },
        )
    )


def _seed_reconciliation_task(env: dict[str, Any]) -> None:
    task_ref = BackendRef(RefKind.TASK, "hecate", str(env["task"].id))
    store = env["store"]
    run_ref = BackendRef(RefKind.RUN, "hecate", str(env["run"].id))
    store.submit_task(
        key=IdempotencyKey("existing-submission", str(env["admin_ctx"].user_id), str(DEFAULT_WORKSPACE_ID), "a" * 64),
        task_ref=task_ref,
        run_ref=run_ref,
        input_payload={"goal": "existing durable input"},
    )
    store.apply_task_state(task_ref, TaskLifecycleState.RUNNING, expected_revision=0, event_run_ref=run_ref)
    store.apply_task_state(
        task_ref,
        TaskLifecycleState.RECONCILIATION_REQUIRED,
        expected_revision=1,
        event_run_ref=run_ref,
        extra_update={"dispatch_attempts": 2, "last_error": "existing failure"},
    )


async def _list_commands(env: dict[str, Any], task_id: uuid.UUID) -> Any:
    return await env["client"].get(f"/api/tasks/{task_id}/commands")


async def _read_command(env: dict[str, Any], command_id: str) -> Any:
    return await env["client"].get(f"/api/commands/{command_id}")


async def _pending(env: dict[str, Any]) -> Any:
    return await env["client"].get("/api/reconciliation/pending")


def _command_states(env: dict[str, Any]) -> dict[str, str]:
    items, _total = env["store"].list_task_commands(
        str(DEFAULT_WORKSPACE_ID),
        BackendRef(RefKind.TASK, "hecate", str(env["task"].id)),
        100,
        0,
    )
    return {record.command_id: record.state.value for record in items}


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
# Authorized parity: VIEWER reads exactly what EDITOR and ADMIN read
# ---------------------------------------------------------------------------


async def test_all_three_entries_succeed_identically_for_every_read_role(env):
    """VIEWER, EDITOR and ADMIN get identical bodies on all three entries."""
    _seed_command(env, "cmd-open")
    _seed_command(env, "cmd-applied")
    env["store"].transition("cmd-applied", CommandState.APPLIED)
    _seed_reconciliation_task(env)
    per_role: dict[str, dict[str, Any]] = {}
    for role in (WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN):
        _as(env, replace(env["admin_ctx"], role=role))
        listing = await _list_commands(env, env["task"].id)
        single = await _read_command(env, "cmd-open")
        pending = await _pending(env)
        assert listing.status_code == single.status_code == pending.status_code == 200, (
            role,
            listing.text,
            single.text,
            pending.text,
        )
        per_role[role.value] = {
            "list": listing.json(),
            "single": single.json(),
            "pending": pending.json(),
        }
    roles = (WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN)
    viewer, editor, admin = (per_role[role.value] for role in roles)
    assert viewer == editor == admin
    assert {item["command_id"] for item in viewer["list"]["items"]} == {"cmd-open", "cmd-applied"}
    assert any(entry["task_ref"]["id"] == str(env["task"].id) for entry in viewer["pending"]["tasks"])


# ---------------------------------------------------------------------------
# Visibility: unified 404 before the role floor and before recorder writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None])
async def test_cross_workspace_command_read_matches_missing(env, second_workspace, role):
    """A foreign identity cannot distinguish an existing command from none."""
    _seed_command(env, "cmd-w1", expires_at=datetime.now(UTC) - timedelta(days=1))
    _as(env, replace(second_workspace[1], role=role))
    foreign = await _read_command(env, "cmd-w1")
    missing = await _read_command(env, "cmd-does-not-exist")
    assert foreign.status_code == missing.status_code == 404
    assert _not_found_shape(foreign, "cmd-w1") == _not_found_shape(missing, "cmd-does-not-exist")
    assert _command_states(env)["cmd-w1"] == "requested"
    assert "transition" not in env["calls"]


def _not_found_shape(response, resource_id: str) -> dict[str, Any]:
    body = response.json()
    assert body == {
        "detail": {
            "error": {
                "code": "NOT_FOUND",
                "message": f"command {resource_id} not found in workspace",
                "details": None,
            }
        }
    }
    body["detail"]["error"]["message"] = "command {id} not found in workspace"
    return body


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("case", ["missing_task", "foreign_workspace", "deleted_task", "moved_task"])
async def test_invisible_task_command_list_matches_missing(env, second_workspace, case, role):
    """The list entry 404s before any recorder access, uniformly."""
    task_id = env["task"].id
    if case == "missing_task":
        task_id = uuid.uuid4()
    elif case == "foreign_workspace":
        _as(env, replace(second_workspace[1], role=role))
    elif case == "deleted_task":
        env["task"].deleted = True
        await env["db"].commit()
    elif case == "moved_task":
        env["task"].workspace_id = uuid.uuid4()
        await env["db"].commit()
    if case != "foreign_workspace":
        _as(env, replace(env["admin_ctx"], role=role))
    calls_before = list(env["calls"])
    response = await _list_commands(env, task_id)
    missing = await _list_commands(env, uuid.uuid4())
    assert response.status_code == missing.status_code == 404
    assert response.json()["detail"]["error"] == {
        "code": "NOT_FOUND",
        "message": f"task {task_id} not found in workspace",
        "details": None,
    }
    assert env["calls"] == calls_before


# ---------------------------------------------------------------------------
# Role floor and workspace floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["list", "single", "pending"])
async def test_membership_without_role_is_forbidden(env, entry):
    """A same-workspace identity with no membership role reads nothing."""
    _seed_command(env, "cmd-open")
    _as(env, replace(env["admin_ctx"], role=None))
    request = {
        "list": lambda: _list_commands(env, env["task"].id),
        "single": lambda: _read_command(env, "cmd-open"),
        "pending": lambda: _pending(env),
    }[entry]
    response = await request()
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("entry", ["list", "single", "pending"])
async def test_forged_query_role_does_not_escalate(env, entry):
    """Client-supplied role hints in the query string are ignored."""
    _seed_command(env, "cmd-open")
    _as(env, replace(env["admin_ctx"], role=None))
    responses = {
        "list": await env["client"].get(f"/api/tasks/{env['task'].id}/commands?role=admin"),
        "single": await env["client"].get("/api/commands/cmd-open?role=admin"),
        "pending": await env["client"].get("/api/reconciliation/pending?role=admin"),
    }
    assert responses[entry].status_code == 403, responses[entry].text
    assert responses[entry].json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert _command_states(env)["cmd-open"] == "requested"


@pytest.mark.parametrize("entry", ["list", "single", "pending"])
async def test_missing_workspace_identity_keeps_existing_rejection(env, entry):
    """No workspace: 403 NO_WORKSPACE, exactly as before the gate."""
    _seed_command(env, "cmd-open")
    ghost = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )
    _as(env, ghost)
    responses = {
        "list": await _list_commands(env, env["task"].id),
        "single": await _read_command(env, "cmd-open"),
        "pending": await _pending(env),
    }
    assert responses[entry].status_code == 403, responses[entry].text
    assert responses[entry].json()["detail"]["error"]["code"] == "NO_WORKSPACE"


@pytest.mark.parametrize("entry", ["list", "single", "pending"])
async def test_system_scope_keeps_existing_rejection(env, entry):
    """The bootstrap key gains no global observation query."""
    _seed_command(env, "cmd-open")
    _as(env, _system_ctx())
    responses = {
        "list": await _list_commands(env, env["task"].id),
        "single": await _read_command(env, "cmd-open"),
        "pending": await _pending(env),
    }
    assert responses[entry].status_code == 403, responses[entry].text
    assert responses[entry].json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    assert _command_states(env)["cmd-open"] == "requested"


# ---------------------------------------------------------------------------
# Lazy expiry stays bound to authorization
# ---------------------------------------------------------------------------


async def test_denials_do_not_advance_a_lapsed_requested_command(env):
    """A truly expired-but-requested receipt survives every rejection."""
    past = datetime.now(UTC) - timedelta(days=1)
    _seed_command(env, "cmd-lapsed", expires_at=past)
    _seed_reconciliation_task(env)
    assert _command_states(env)["cmd-lapsed"] == "requested"

    # VIEWER is the read floor: its authorized read DOES lazily expire the
    # receipt, so the denial cases use a role-less identity instead.
    _as(env, replace(env["admin_ctx"], role=None))
    calls_seeded = len(env["calls"])
    single_denied = await _read_command(env, "cmd-lapsed")
    list_denied = await _list_commands(env, env["task"].id)
    pending_denied = await _pending(env)
    assert single_denied.status_code == list_denied.status_code == pending_denied.status_code == 403
    # The single-read pre-check may consult the recorder; nothing beyond
    # the pure lookup, and never the expiry sweep, may run.
    assert set(env["calls"][calls_seeded:]) <= {"get"}
    assert _command_states(env)["cmd-lapsed"] == "requested"


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_authorized_reads_keep_recorded_and_lazy_expiry_semantics(env, second_workspace, role):
    """Verbatim list, lazy single read, sweep scoped to the workspace."""
    past = datetime.now(UTC) - timedelta(days=1)
    _seed_command(env, "cmd-lapsed", expires_at=past)
    _seed_command(env, "cmd-sweep", expires_at=past)
    _seed_command(env, "cmd-foreign-ws", workspace_id=second_workspace[0].id, expires_at=past)

    _as(env, replace(env["admin_ctx"], role=role))
    listing = await _list_commands(env, env["task"].id)
    assert listing.status_code == 200, listing.text
    states = {item["command_id"]: item["state"] for item in listing.json()["items"]}
    assert states["cmd-lapsed"] == "requested"
    assert all(set(item) == COMMAND_WHITELIST for item in listing.json()["items"])
    assert "relay.status" not in env["calls"] and "relay.pump" not in env["calls"]

    single = await _read_command(env, "cmd-lapsed")
    assert single.status_code == 200, single.text
    assert single.json()["state"] == "expired"
    assert set(single.json()) == COMMAND_WHITELIST
    assert not any(
        secret in single.text for secret in ("secret-command-payload", "secret-command-detail", "secret-command-extra")
    )
    assert _command_states(env)["cmd-lapsed"] == "expired"

    pending = await _pending(env)
    assert pending.status_code == 200, pending.text
    swept = {entry["command_id"] for entry in pending.json()["commands"]}
    assert "cmd-sweep" in swept
    assert "cmd-foreign-ws" not in swept
    assert "cmd-lapsed" not in swept  # already expired; the sweep only converges lapsed receipts
    stored = _command_states(env)
    assert stored["cmd-sweep"] == "expired"
    assert env["store"].get("cmd-foreign-ws").state is CommandState.REQUESTED
    assert pending.json()["workspace_id"] == str(DEFAULT_WORKSPACE_ID)
    assert env["calls"].count("relay.status") == 1
    assert pending.json()["event_relay"]["relay_key"] == "test"
    assert "relay.pump" not in env["calls"] and "dispatch" not in env["calls"]


async def test_reconciliation_is_scoped_to_the_callers_workspace(env, second_workspace):
    """A foreign viewer queries their own workspace, never W1's facts."""
    past = datetime.now(UTC) - timedelta(days=1)
    _seed_command(env, "cmd-w1-lapsed", expires_at=past)
    _seed_reconciliation_task(env)
    _as(env, second_workspace[1])
    response = await _pending(env)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace_id"] == str(second_workspace[0].id)
    assert body["tasks"] == []
    assert body["commands"] == []
    assert _command_states(env)["cmd-w1-lapsed"] == "requested"


# ---------------------------------------------------------------------------
# Side-effect freedom of every rejection on populated SQL facts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry,case",
    [
        (entry, case)
        for entry, cases in (
            ("list", ("foreign", "missing", "deleted_task", "no_role", "no_workspace", "system")),
            ("single", ("foreign", "missing", "no_role", "no_workspace", "system")),
            ("pending", ("no_role", "no_workspace", "system")),
        )
        for case in cases
    ],
)
async def test_rejected_requests_leave_all_state_untouched(env, second_workspace, entry, case):
    """Applicable denials preserve populated state, events, outbox and lapsed receipts."""
    _seed_command(env, "cmd-seeded", expires_at=datetime.now(UTC) - timedelta(days=1))
    _seed_reconciliation_task(env)
    task_id, command_id = env["task"].id, "cmd-seeded"
    ctx = replace(env["admin_ctx"], role=WorkspaceRole.VIEWER)
    if case == "foreign":
        ctx = second_workspace[1]
    elif case == "missing":
        task_id = uuid.uuid4()
        if entry == "single":
            command_id = "cmd-not-seeded-anywhere"
    elif case == "deleted_task":
        env["task"].deleted = True
        await env["db"].commit()
    elif case == "no_role":
        ctx = replace(ctx, role=None)
    elif case == "no_workspace":
        ctx = replace(ctx, workspace_id=None, role=None)
    elif case == "system":
        ctx = _system_ctx()
    _as(env, ctx)
    before = await _persistent_snapshot(env)
    assert before["tasks"] and before["runs"]
    assert before["durable_task_state"] and before["durable_command"]
    assert before["platform_events"] and before["durable_event_log"] and before["durable_submission"]
    calls_before = list(env["calls"])
    response = await {
        "list": lambda: _list_commands(env, task_id),
        "single": lambda: _read_command(env, command_id),
        "pending": lambda: _pending(env),
    }[entry]()
    expected = 404 if case in {"foreign", "missing", "deleted_task"} else 403
    assert response.status_code == expected, (case, entry, response.text)
    assert await _persistent_snapshot(env) == before
    new_calls = set(env["calls"][len(calls_before) :])
    # The single-command pre-check is a pure recorder.get; every other
    # entry rejects before touching the command store.
    allowed = {"get"} if entry == "single" and case not in {"no_workspace", "system"} else set()
    assert new_calls <= allowed, (case, entry, new_calls)
    assert "transition" not in new_calls
    assert "dispatch" not in new_calls
