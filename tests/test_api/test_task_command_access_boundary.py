"""Task control-command write access boundary (step14).

Covers the two-level write gate on the control-command REST entries
(``POST /api/tasks/{id}/commands`` and ``POST /api/tasks/{id}/cancel``):
visibility first — foreign, missing and soft-deleted tasks surface as the
same 404 — then a server-side EDITOR role floor. The gate precedes
command storage, idempotent replay and outbox delivery, so a downgraded
user cannot replay an old command_id and every rejected request is
side-effect free. Existing approval/revision/expiry semantics survive the
gate, and the previous behavior for workspace-less and system-scope
identities is preserved verbatim.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from hecate.channel.api.tasks import router
from hecate.contracts.execution.durable import IdempotencyKey, TaskLifecycleState
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.task_control import TaskControlService
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.conftest import DEFAULT_WORKSPACE_ID


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
    """W1 (default workspace) task under an SQL suite with swappable auth."""
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="command-boundary")
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
            workload_id="hecate:command-boundary",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="boundary task",
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
    await db_session.commit()
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'command-boundary.db'}", source="platform")
    store.create_schema()
    calls: list[str] = []
    for name in ("get", "record", "transition"):
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

    async def shared_db():
        yield db_session

    async def swapped_auth():
        return holder["ctx"]

    app.dependency_overrides[get_db] = shared_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "db": db_session,
                "store": store,
                "task": task,
                "run": run,
                "calls": calls,
                "registry": registry,
                "chain": chain.to_dict(),
                "holder": holder,
                "admin_ctx": auth_context,
            }
    finally:
        app.dependency_overrides.clear()
        store.dispose()


def _as(env: dict[str, Any], ctx: AuthContext) -> None:
    env["holder"]["ctx"] = ctx


async def _commands(env: dict[str, Any], task_id: uuid.UUID, *, body: dict[str, Any] | None = None):
    payload = body or {"kind": "cancel"}
    return await env["client"].post(f"/api/tasks/{task_id}/commands", json=payload)


async def _cancel(env: dict[str, Any], task_id: uuid.UUID) -> Any:
    return await env["client"].post(f"/api/tasks/{task_id}/cancel")


def _receipts(env: dict[str, Any], task_id: uuid.UUID) -> list[Any]:
    items, _total = env["store"].list_task_commands(
        str(DEFAULT_WORKSPACE_ID),
        BackendRef(RefKind.TASK, "hecate", str(task_id)),
        100,
        0,
    )
    return items


def _snapshot(env: dict[str, Any], task_id: uuid.UUID) -> list[Any]:
    return list(_receipts(env, task_id))


def _issue_body(command_id: str, kind: str = "cancel") -> dict[str, Any]:
    return {"kind": kind, "command_id": command_id}


# ---------------------------------------------------------------------------
# Visibility: foreign, missing and soft-deleted are one 404
# ---------------------------------------------------------------------------


@pytest.fixture
async def second_workspace(db_session):
    """A second organization + workspace with an editor member."""
    user_id = uuid.uuid4()
    org = OrganizationModel(name="Org foreign", slug=f"org-foreign-{uuid.uuid4().hex[:8]}", owner_id=user_id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name="ws-foreign", slug=f"ws-foreign-{uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=WorkspaceRole.EDITOR))
    await db_session.commit()
    return ws, replace(_ctx_for(ws, WorkspaceRole.EDITOR), user_id=user_id)


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("entry", ["commands", "cancel"])
async def test_cross_workspace_commands_surface_as_not_found(env, second_workspace, role, entry) -> None:
    """Invisible resources return 404 before even a missing role is checked."""
    _as(env, replace(second_workspace[1], role=role))
    task_id, missing_id = env["task"].id, uuid.uuid4()
    request = _commands if entry == "commands" else _cancel
    probe = await request(env, task_id)
    reference = await request(env, missing_id)
    assert probe.status_code == reference.status_code == 404
    assert _not_found_shape(probe, task_id) == _not_found_shape(reference, missing_id)
    assert env["calls"] == []
    assert _receipts(env, task_id) == []


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("entry", ["commands", "cancel"])
async def test_soft_deleted_task_matches_missing(env, role, entry) -> None:
    """Soft-deleted tasks have the same envelope as missing tasks on both routes."""
    task = env["task"]
    task.deleted = True
    await env["db"].commit()
    _as(env, replace(env["admin_ctx"], role=role))
    request = _commands if entry == "commands" else _cancel
    missing_id = uuid.uuid4()
    deleted = await request(env, task.id)
    missing = await request(env, missing_id)
    assert deleted.status_code == missing.status_code == 404
    assert _not_found_shape(deleted, task.id) == _not_found_shape(missing, missing_id)
    assert env["calls"] == []


def _not_found_shape(response, task_id) -> dict[str, Any]:
    body = response.json()
    assert body["detail"]["error"]["code"] == "NOT_FOUND"
    body["detail"]["error"]["message"] = body["detail"]["error"]["message"].replace(str(task_id), "{task_id}")
    return body


# ---------------------------------------------------------------------------
# Role floor: viewer denied, editor/admin proceed
# ---------------------------------------------------------------------------


async def test_viewer_is_forbidden_on_both_entries(env, db_session):
    """Same-workspace viewer gets 403 with zero side effects."""
    viewer = _ctx_for(
        await db_session.get(WorkspaceModel, DEFAULT_WORKSPACE_ID),
        WorkspaceRole.VIEWER,
    )
    _as(env, viewer)
    before = _snapshot(env, env["task"].id)

    commands = await _commands(env, env["task"].id, body=_issue_body("cmd-viewer"))
    cancel = await _cancel(env, env["task"].id)

    assert commands.status_code == 403, commands.text
    assert cancel.status_code == 403, cancel.text
    assert commands.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert _snapshot(env, env["task"].id) == before


async def test_membership_without_role_is_forbidden(env, db_session):
    """Same-workspace identity with no membership role cannot write."""
    no_role = _ctx_for(
        await db_session.get(WorkspaceModel, DEFAULT_WORKSPACE_ID),
        None,
    )
    _as(env, no_role)

    commands = await _commands(env, env["task"].id, body=_issue_body("cmd-no-role"))

    assert commands.status_code == 403, commands.text
    assert commands.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert _receipts(env, env["task"].id) == []


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
@pytest.mark.parametrize("entry", ["commands", "cancel"])
async def test_editor_and_admin_issue_commands(env, role, entry):
    """Both authorized roles reach both command routes with the same issuer."""
    ctx = replace(env["admin_ctx"], role=role)
    _as(env, ctx)
    if entry == "cancel":
        response = await _cancel(env, env["task"].id)
        assert response.status_code == 200, response.text
    else:
        response = await _commands(env, env["task"].id, body=_issue_body("cmd-editor"))
        assert response.status_code == 201, response.text
    assert response.json()["state"] == "requested"
    assert response.json()["issuer"] == str(ctx.user_id)
    stored = _receipts(env, env["task"].id)
    assert len(stored) == 1
    assert stored[0].command_id == response.json()["command_id"]
    assert env["calls"]


# ---------------------------------------------------------------------------
# Identity rules: missing workspace and system scope stay as before
# ---------------------------------------------------------------------------


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

    commands = await _commands(env, env["task"].id, body=_issue_body("cmd-ghost"))

    assert commands.status_code == 403, commands.text
    assert commands.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    assert _receipts(env, env["task"].id) == []


async def test_system_scope_keeps_existing_rejection(env):
    """The bootstrap key gains no cross-workspace command capability."""
    _as(env, _system_ctx())

    commands = await _commands(env, env["task"].id, body=_issue_body("cmd-system"))

    assert commands.status_code == 403, commands.text
    assert commands.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    assert _receipts(env, env["task"].id) == []


# ---------------------------------------------------------------------------
# Downgrade replay and side-effect freedom
# ---------------------------------------------------------------------------


async def test_downgraded_user_cannot_replay_old_command_id(env):
    """Role gate precedes idempotent replay; the receipt stays untouched."""
    editor = replace(env["admin_ctx"], role=WorkspaceRole.EDITOR)
    _as(env, editor)
    body = _issue_body("cmd-downgrade", kind="pause")
    first = await _commands(env, env["task"].id, body=body)
    assert first.status_code == 201, first.text
    assert first.json()["state"] == "rejected"
    receipt_before = _snapshot(env, env["task"].id)
    calls_before = list(env["calls"])

    _as(env, replace(editor, role=WorkspaceRole.VIEWER))
    replay = await _commands(env, env["task"].id, body=body)

    assert replay.status_code == 403, replay.text
    assert replay.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert _snapshot(env, env["task"].id) == receipt_before
    assert env["calls"] == calls_before


async def db_workspace(env) -> WorkspaceModel:
    """The default workspace row for membership lookups."""
    return await env["db"].get(WorkspaceModel, DEFAULT_WORKSPACE_ID)


async def _persistent_snapshot(env) -> dict[str, Any]:
    snapshot = {}
    for model in (TaskModel, RunModel):
        table = model.__table__
        rows = await env["db"].execute(select(table).order_by(*table.primary_key.columns))
        snapshot[table.name] = deepcopy([dict(row) for row in rows.mappings()])
    with env["store"].engine.connect() as conn:
        for table in DurableBase.metadata.sorted_tables:
            rows = conn.execute(select(table).order_by(*table.primary_key.columns))
            snapshot[table.name] = deepcopy([dict(row) for row in rows.mappings()])
    return snapshot


@pytest.mark.parametrize("entry", ["cancel", "commands"])
@pytest.mark.parametrize("case", ["foreign", "missing", "deleted", "viewer", "no_role", "no_workspace", "system"])
async def test_rejected_requests_leave_task_and_outbox_untouched(env, second_workspace, entry, case):
    """Denials never read/replay the recorder or alter populated SQL facts."""
    task = env["task"]
    seeded = await _commands(env, task.id, body=_issue_body("existing-receipt", kind="pause"))
    assert seeded.status_code == 201
    task_ref = BackendRef(RefKind.TASK, "hecate", str(task.id))
    env["store"].submit_task(
        key=IdempotencyKey(
            key="seeded-submission",
            subject=str(env["admin_ctx"].user_id),
            workspace=str(DEFAULT_WORKSPACE_ID),
            request_digest="a" * 64,
        ),
        task_ref=task_ref,
        run_ref=BackendRef(RefKind.RUN, "hecate", str(env["run"].id)),
        input_payload={"messages": [{"role": "user", "content": "existing task input"}]},
    )
    target = task.id
    ctx = replace(env["admin_ctx"], role=WorkspaceRole.VIEWER)
    if case == "foreign":
        ctx = replace(second_workspace[1], role=WorkspaceRole.VIEWER)
    elif case == "missing":
        target = uuid.uuid4()
    elif case == "deleted":
        task.deleted = True
        await env["db"].commit()
    elif case == "no_role":
        ctx = replace(ctx, role=None)
    elif case == "no_workspace":
        ctx = replace(ctx, workspace_id=None, role=None)
    elif case == "system":
        ctx = _system_ctx()
    _as(env, ctx)
    before = await _persistent_snapshot(env)
    assert before["tasks"] and before["runs"] and before["durable_task_state"]
    assert before["durable_command"] and before["durable_event_log"]
    calls_before = list(env["calls"])
    response = (
        await _cancel(env, target)
        if entry == "cancel"
        else await _commands(env, target, body=_issue_body("existing-receipt", kind="pause"))
    )
    expected = 404 if case in {"foreign", "missing", "deleted"} else 403
    assert response.status_code == expected, response.text
    assert await _persistent_snapshot(env) == before
    assert env["calls"] == calls_before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("kind", ["cancel", "pause", "resume", "provide_input"])
async def test_every_command_kind_requires_server_side_editor(env, role, kind):
    """Client-supplied roles cannot override the server role for any kind."""
    _as(env, replace(env["admin_ctx"], role=role))
    before = await _persistent_snapshot(env)
    response = await _commands(env, env["task"].id, body={"kind": kind, "role": "admin"})
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert env["calls"] == []
    assert await _persistent_snapshot(env) == before


# ---------------------------------------------------------------------------
# Existing command semantics survive the gate
# ---------------------------------------------------------------------------


async def test_editor_idempotent_replay_still_reuses_receipt(env):
    """Authorized same-body reissue replays the same receipt idempotently."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    body = _issue_body("cmd-replay", kind="pause")

    first = await _commands(env, env["task"].id, body=body)
    before = _snapshot(env, env["task"].id)
    second = await _commands(env, env["task"].id, body=body)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["command_id"] == second.json()["command_id"] == "cmd-replay"
    assert first.json()["issued_at"] == second.json()["issued_at"]
    assert len(before) == 1
    assert _snapshot(env, env["task"].id) == before
    assert first.json()["state"] == second.json()["state"] == "rejected"


async def test_editor_still_honors_revision_and_expiry_semantics(env):
    """EDITOR does not exempt a command from revision/expiry validation."""
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))

    stale_revision = await _commands(
        env,
        env["task"].id,
        body={"kind": "cancel", "command_id": "cmd-rev", "expected_revision": 999},
    )
    expired = await _commands(
        env,
        env["task"].id,
        body={
            "kind": "cancel",
            "command_id": "cmd-expired",
            "expires_at": "2000-01-01T00:00:00+00:00",
        },
    )

    assert stale_revision.status_code == 201, stale_revision.text
    assert expired.status_code == 201, expired.text
    states = {item.command_id: item.state.value for item in _receipts(env, env["task"].id)}
    assert states["cmd-rev"] == "rejected"
    assert states["cmd-expired"] == "expired"


async def test_editor_cannot_replace_approval_authority_with_a_wake_token(env):
    """A valid EDITOR and token still cannot resume without an approval fact."""
    task_ref = BackendRef(RefKind.TASK, "hecate", str(env["task"].id))
    store = env["store"]
    store.apply_task_state(task_ref, TaskLifecycleState.QUEUED)
    store.apply_task_state(task_ref, TaskLifecycleState.RUNNING)
    store.apply_task_state(
        task_ref,
        TaskLifecycleState.WAITING_APPROVAL,
        extra_update={
            "wait": {
                "wait_token": "valid-transport-token",
                "consumed": False,
                "contract_ref": {"approval_ref": "missing-authority"},
            }
        },
    )
    state_before = store.get_task_state(task_ref)
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    response = await _commands(
        env,
        env["task"].id,
        body={
            "kind": "resume",
            "command_id": "unapproved-resume",
            "detail_ns": {"wait_token": "valid-transport-token"},
            "payload": {"approved": True, "approval_ref": "missing-authority"},
            "payload_schema_ref": "test:approval-resume/0",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "rejected"
    assert "no approval authority record" in response.json()["detail"]
    assert store.get_task_state(task_ref) == state_before
    assert "dispatch" not in env["calls"]
