"""Workflow callback REST write access boundary (step14).

``POST /api/tasks/{id}/workflow-callback`` gets the same two-level write
gate as the other control-command entries: active workspace, then parent
visibility (foreign, missing and soft-deleted tasks surface as the same
404), then the server-side EDITOR floor — all ahead of any receipt
replay, child-fact verification, wait-token consumption or receipt
write. A wait token or a settled command_id is a fact about the task,
never a substitute for write permission, so a downgraded user cannot
replay an applied or rejected callback. Trusted internal callers keep
consuming the service directly. Authorized callers keep the verified
callback semantics: real child facts, one wake per token, idempotent
receipt replay.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.api.tasks import router
from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyKey,
)
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.task_control import _background_tasks, task_ref_of
from hecate.execution.task_dispatcher import PlatformTaskDispatcher, TaskWaitingSignalError
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
from tests.conftest import test_session_factory as shared_session_factory


def _ctx_for(ws: WorkspaceModel, role: WorkspaceRole | None) -> AuthContext:
    """A workspace-scoped JWT identity with the given membership role."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


def _system_ctx(workspace: WorkspaceModel | None = None) -> AuthContext:
    """Deploy-time bootstrap identity, optionally bound to a workspace."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=workspace.org_id if workspace else None,
        workspace_id=workspace.id if workspace else None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


class _EntryDouble:
    """Callable entry boundary: every execution succeeds (callables do not
    bind like functions)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.calls.append(kwargs)
        return {"status": "succeeded", "content": "callback boundary stub reply"}


@pytest.fixture
async def env(db_session, auth_context, tmp_path, monkeypatch) -> AsyncGenerator[dict, None]:
    """W1 agent + boundary task under an isolated SQL suite with swappable auth.

    ``PlatformTaskDispatcher._execute`` runs through an entry double so the
    authorized-path tests can drive real submissions to terminal states and
    park a parent on a durable child wait without any model dependency.
    """
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="callback-boundary")
    db_session.add(agent)
    await db_session.flush()
    version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="a" * 64)
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
            workload_id="hecate:callback-boundary",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="callback boundary task",
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

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'callback-boundary.db'}", source="platform")
    store.create_schema()
    # The submission pipeline row-locks TaskStateRow through the APP session
    # when the bound store is SQL-backed (sql_store=hasattr(store, "engine")).
    # The lock query only needs the durable tables to EXIST on the shared
    # test engine — the durable facts themselves live in the file store above.
    assert db_session.bind is not None
    async with db_session.bind.begin() as conn:
        await conn.run_sync(DurableBase.metadata.create_all)
    calls: list[str] = []
    for name in ("get", "record", "transition"):
        original = getattr(store, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observed)
    monkeypatch.setattr(
        "hecate.channel.api.tasks.get_durable_suite",
        lambda: DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core"),
    )

    entry = _EntryDouble()
    real_execute = PlatformTaskDispatcher._execute

    async def entry_execute(self, db, context, **kwargs):
        return await entry(db, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", entry_execute)

    # Production-shaped dispatch wiring: a durable worker (own sessions via
    # the shared test factory) bound to the app-state handle the route reads.
    # Background requeues then never reuse a finished request's session, and
    # lease arbitration goes through exactly one worker.
    from hecate_durable.worker import DurableWorker

    from hecate.channel.api import tasks as tasks_api

    dispatcher = PlatformTaskDispatcher(store, shared_session_factory)
    worker = DurableWorker(store, dispatcher, leases=store.leases)
    tasks_api.bind_worker_state(SimpleNamespace(durable_worker=worker, durable_outbox_relay=None))

    holder: dict[str, AuthContext] = {"ctx": auth_context}
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def per_request_db() -> AsyncGenerator[AsyncSession, None]:
        # A fresh session per request: a wake's background dispatch outlives
        # the callback request and must not reuse a settled transaction.
        async with shared_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def swapped_auth():
        return holder["ctx"]

    app.dependency_overrides[get_db] = per_request_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    previous_tasks = set(_background_tasks)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "db": db_session,
                "store": store,
                "task": task,
                "run": run,
                "agent_id": agent.id,
                "calls": calls,
                "holder": holder,
                "admin_ctx": auth_context,
                "entry": entry,
            }
    finally:
        # Wake requeues run through the bound worker; keep the entry double
        # installed until those tasks leave the shared test database. A
        # straggler's dispatch failure is inert here (the durable facts under
        # assertion were written synchronously), so exceptions don't abort
        # the drain — completed tasks leave the set either way.
        async with asyncio.timeout(20):
            while pending := _background_tasks - previous_tasks:
                await asyncio.gather(*pending, return_exceptions=True)
        tasks_api.bind_worker_state(None)
        monkeypatch.setattr(PlatformTaskDispatcher, "_execute", real_execute, raising=False)
        app.dependency_overrides.clear()
        store.dispose()


@pytest.fixture
async def second_workspace(db_session):
    """A second organization + workspace with an editor member."""
    user_id = uuid.uuid4()
    org = OrganizationModel(name="Org cb-foreign", slug=f"org-cb-{uuid.uuid4().hex[:8]}", owner_id=user_id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name="ws-cb-foreign", slug=f"ws-cb-{uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=WorkspaceRole.EDITOR))
    await db_session.commit()
    return ws, replace(_ctx_for(ws, WorkspaceRole.EDITOR), user_id=user_id)


def _as(env: dict[str, Any], ctx: AuthContext) -> None:
    env["holder"]["ctx"] = ctx


async def _callback(
    env: dict[str, Any],
    task_id: uuid.UUID | str,
    body: dict[str, Any] | None = None,
) -> Any:
    payload = body or {
        "command_id": f"cb-{uuid.uuid4()}",
        "wait_token": "token-does-not-grant-permission",
        "child_task_id": str(uuid.uuid4()),
        "declared_state": "succeeded",
        "result": {},
    }
    return await env["client"].post(f"/api/tasks/{task_id}/workflow-callback", json=payload)


def _receipts(env: dict[str, Any], task_id: uuid.UUID) -> list[Any]:
    items, _total = env["store"].list_task_commands(
        str(DEFAULT_WORKSPACE_ID),
        BackendRef(RefKind.TASK, "hecate", str(task_id)),
        100,
        0,
    )
    return items


def _seed_settled_receipt(env: dict[str, Any], task_id: uuid.UUID, command_id: str, state: CommandState) -> None:
    env["store"].record(
        ControlCommandRecord(
            command_id=command_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer="seed",
            task_ref=BackendRef(RefKind.TASK, "hecate", str(task_id)),
            issued_at="2026-10-25T00:00:00+00:00",
            state=state,
            extra={"workspace_id": str(DEFAULT_WORKSPACE_ID)},
            detail_ns={"callback_digest": "seeded"},
        )
    )


async def _persistent_snapshot(env: dict[str, Any]) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Real workflow facts for the authorized path: a terminal child and a parent
# parked on a durable wait whose contract names that child.
# ---------------------------------------------------------------------------


async def _submit(env: dict[str, Any], goal: str, **overrides) -> dict:
    body = {
        "goal": goal,
        "agent_id": str(env["agent_id"]),
        "input": {"messages": [{"role": "user", "content": goal}]},
    }
    body.update(overrides)
    response = await env["client"].post("/api/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_terminal(env: dict[str, Any], task_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = (await env["client"].get(f"/api/tasks/{task_id}")).json()
        if detail["lifecycle_state"] not in ("queued", "running", "unrecorded"):
            return detail
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


async def _terminal_child_with_parked_parent(env: dict[str, Any], monkeypatch) -> dict:
    """Submit a child, let it finish, then park the parent waiting on it."""

    child = await _submit(env, "callback boundary child")
    await _wait_terminal(env, child["task_id"])
    child_task_id = child["task_id"]

    async def wait_for_child(self, db, context, **kwargs):
        raise TaskWaitingSignalError(
            ControlCommandKind.PROVIDE_INPUT,
            {"await_task_ref": {"issuer_domain": "hecate", "id": child_task_id, "kind": "task"}},
            expires_in_seconds=600,
        )

    current = PlatformTaskDispatcher._execute
    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", wait_for_child)
    try:
        parent = await _submit(env, "callback boundary parent", wait=True)
    finally:
        monkeypatch.setattr(PlatformTaskDispatcher, "_execute", current, raising=False)
    assert parent["lifecycle_state"] == "waiting_input", parent
    detail = (await env["client"].get(f"/api/tasks/{parent['task_id']}")).json()
    assert detail["wait"]["contract_ref"]["await_task_ref"]["id"] == child_task_id
    return {"parent": parent, "child": child, "wait": detail["wait"]}


def _callback_body(facts: dict, **overrides) -> dict[str, Any]:
    body = {
        "command_id": f"cb-{uuid.uuid4()}",
        "wait_token": facts["wait"]["wait_token"],
        "child_task_id": facts["child"]["task_id"],
        "declared_state": "succeeded",
        "result": {"note": "child done"},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Visibility and role floor
# ---------------------------------------------------------------------------


def _not_found_shape(response, task_id) -> dict[str, Any]:
    body = response.json()
    assert body["detail"]["error"]["code"] == "NOT_FOUND"
    body["detail"]["error"]["message"] = body["detail"]["error"]["message"].replace(str(task_id), "{task_id}")
    return body


async def test_cross_workspace_callback_indistinguishable_from_missing(env, second_workspace):
    """Foreign parents surface as the same 404 as missing ones, any role."""
    for role in (WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None):
        _as(env, replace(second_workspace[1], role=role))
        missing_id = uuid.uuid4()
        probe = await _callback(env, env["task"].id)
        missing = await _callback(env, missing_id)
        assert probe.status_code == missing.status_code == 404
        assert _not_found_shape(probe, env["task"].id) == _not_found_shape(missing, missing_id)
    assert env["calls"] == []


async def test_soft_deleted_parent_matches_missing(env):
    """Soft-deleted parents keep the missing-task envelope on this route."""
    task = env["task"]
    task.deleted = True
    await env["db"].commit()
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    missing_id = uuid.uuid4()
    deleted = await _callback(env, task.id)
    missing = await _callback(env, missing_id)
    assert deleted.status_code == missing.status_code == 404
    assert _not_found_shape(deleted, task.id) == _not_found_shape(missing, missing_id)
    assert env["calls"] == []


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_viewer_and_roleless_are_forbidden_on_a_visible_parent(env, db_session, role):
    """A visible parent is not enough: the write needs the EDITOR floor."""
    ctx = _ctx_for(await db_session.get(WorkspaceModel, DEFAULT_WORKSPACE_ID), role)
    _as(env, ctx)
    before = _persistent_receipts(env)

    response = await _callback(env, env["task"].id)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert _persistent_receipts(env) == before


def _persistent_receipts(env: dict[str, Any]) -> list[Any]:
    return list(_receipts(env, env["task"].id))


# ---------------------------------------------------------------------------
# Identity rules: missing workspace and system scope
# ---------------------------------------------------------------------------


async def test_workspaceless_identities_are_denied_before_any_access(env):
    """JWT and system identities without a workspace keep NO_WORKSPACE."""
    for ctx in (
        replace(env["admin_ctx"], workspace_id=None, role=None),
        _system_ctx(),
    ):
        _as(env, ctx)
        response = await _callback(env, env["task"].id)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    assert env["calls"] == []


async def test_system_scope_with_workspace_passes_gate_but_stays_scoped(env, db_session, second_workspace):
    """A bootstrap key bound to a workspace keeps its role exemption and its
    resource attribution; one bound elsewhere sees our task as missing."""
    own_ws = await db_session.get(WorkspaceModel, DEFAULT_WORKSPACE_ID)
    _as(env, _system_ctx(own_ws))
    response = await _callback(env, env["task"].id)
    assert response.status_code == 200, response.text
    # The gate passed (no role); business verification still refuses a parent
    # that is not waiting on a child — recorded as an explicit rejection.
    assert response.json()["state"] == "rejected"
    assert "not waiting" in response.json()["detail"]

    _as(env, _system_ctx(second_workspace[0]))
    foreign = await _callback(env, env["task"].id)
    assert foreign.status_code == 404, foreign.text


# ---------------------------------------------------------------------------
# Denials are side-effect free, including settled-receipt replay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["foreign", "missing", "deleted", "viewer", "no_role", "no_workspace", "system"])
async def test_denials_leave_task_and_outbox_untouched(env, db_session, second_workspace, case):
    """No denial path reads/replays the recorder or rewrites durable facts."""
    task = env["task"]
    _seed_settled_receipt(env, task.id, "existing-callback", CommandState.APPLIED)
    env["store"].submit_task(
        key=IdempotencyKey(
            key="seeded-submission",
            subject=str(env["admin_ctx"].user_id),
            workspace=str(DEFAULT_WORKSPACE_ID),
            request_digest="a" * 64,
        ),
        task_ref=BackendRef(RefKind.TASK, "hecate", str(task.id)),
        run_ref=BackendRef(RefKind.RUN, "hecate", str(env["run"].id)),
        input_payload={"messages": [{"role": "user", "content": "existing task input"}]},
    )
    target = task.id
    body = {
        "command_id": "existing-callback",
        "wait_token": "seed-token",
        "child_task_id": str(uuid.uuid4()),
        "declared_state": "succeeded",
    }
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

    response = await _callback(env, target, body=body)

    expected = 404 if case in {"foreign", "missing", "deleted"} else 403
    assert response.status_code == expected, response.text
    assert await _persistent_snapshot(env) == before
    assert env["calls"] == calls_before


# ---------------------------------------------------------------------------
# Wait tokens and settled command ids never replace write permission
# ---------------------------------------------------------------------------


async def test_valid_wait_token_does_not_grant_permission_or_consume(env, db_session, monkeypatch):
    """A VIEWER holding the REAL token cannot wake the parent; the token
    survives the denial and still wakes for an authorized editor."""
    facts = await _terminal_child_with_parked_parent(env, monkeypatch)
    parent_id = facts["parent"]["task_id"]
    viewer = _ctx_for(await db_session.get(WorkspaceModel, DEFAULT_WORKSPACE_ID), WorkspaceRole.VIEWER)
    _as(env, viewer)
    body = _callback_body(facts)
    calls_before = list(env["calls"])

    denied = await _callback(env, parent_id, body=body)

    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert env["calls"] == calls_before  # no recorder/store access at all
    wait = env["store"].get_task_state(task_ref_of(uuid.UUID(parent_id))).extra.get("wait")
    assert wait["consumed"] is False  # untouched by the denied attempt

    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))
    ok = await _callback(env, parent_id, body=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "applied", ok.json()


@pytest.mark.parametrize("settled_state", [CommandState.APPLIED, CommandState.REJECTED])
async def test_downgraded_user_cannot_replay_settled_callback(env, db_session, monkeypatch, settled_state):
    """The role gate precedes idempotent replay for both settled outcomes."""
    facts = await _terminal_child_with_parked_parent(env, monkeypatch)
    parent_id = facts["parent"]["task_id"]
    editor = replace(env["admin_ctx"], role=WorkspaceRole.EDITOR)
    _as(env, editor)
    body = _callback_body(facts)
    if settled_state is CommandState.REJECTED:
        # A truthful mismatch (child really succeeded) settles as rejected.
        body["declared_state"] = "failed"
    first = await _callback(env, parent_id, body=body)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == settled_state.value, first.json()
    receipts_before = _receipts(env, uuid.UUID(parent_id))
    calls_before = list(env["calls"])

    _as(env, replace(editor, role=WorkspaceRole.VIEWER))
    replay = await _callback(env, parent_id, body=body)

    assert replay.status_code == 403, replay.text
    assert replay.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert _receipts(env, uuid.UUID(parent_id)) == receipts_before
    assert env["calls"] == calls_before


# ---------------------------------------------------------------------------
# Authorized callers keep the verified callback semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_authorized_callback_wakes_once_and_replays_idempotently(env, monkeypatch, role):
    """One wake per token from real durable facts, replay settles quietly."""
    facts = await _terminal_child_with_parked_parent(env, monkeypatch)
    parent_id = facts["parent"]["task_id"]
    child_id = facts["child"]["task_id"]
    _as(env, replace(env["admin_ctx"], role=role))
    body = _callback_body(facts)

    first = await _callback(env, parent_id, body=body)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "applied", first.json()

    # Facts from the durable store, not from the HTTP layer: the token is
    # consumed once, the parent left its wait (requeued), and the verified
    # child outcome rode the wake payload.
    parent_ref = task_ref_of(uuid.UUID(parent_id))
    record = env["store"].get_task_state(parent_ref)
    assert record.lifecycle_state.value == "queued"
    wait = (record.extra or {}).get("wait") or {}
    assert wait.get("consumed") is True
    stored_input = env["store"].get_task_input(parent_ref) or {}
    outcome = stored_input.get("provided", {}).get("child_outcome", {})
    assert outcome.get("child_task_id") == child_id
    assert outcome.get("child_state") == "succeeded"

    replay = await _callback(env, parent_id, body=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["state"] == "applied"
    assert replay.json()["detail"] == "idempotent replay of a settled command"
    # Exactly one receipt for the settled command; the wake stays single.
    receipts = [r for r in _receipts(env, uuid.UUID(parent_id)) if r.command_id == body["command_id"]]
    assert len(receipts) == 1
    consumed_after_replay = ((env["store"].get_task_state(parent_ref).extra or {}).get("wait") or {}).get("consumed")
    assert consumed_after_replay is True


async def test_authorized_business_verification_failure_keeps_rejected_receipt(env, monkeypatch):
    """Past the gate, a state mismatch still records an explicit rejection
    and leaves the token unconsumed (existing semantics)."""
    facts = await _terminal_child_with_parked_parent(env, monkeypatch)
    parent_id = facts["parent"]["task_id"]
    _as(env, replace(env["admin_ctx"], role=WorkspaceRole.EDITOR))

    body = _callback_body(facts, declared_state="failed")
    response = await _callback(env, parent_id, body=body)

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "rejected"
    assert "does not match" in response.json()["detail"]
    wait = (env["store"].get_task_state(task_ref_of(uuid.UUID(parent_id))).extra or {}).get("wait") or {}
    assert wait["consumed"] is False


async def test_command_metadata_cannot_forge_verified_workflow_callback(env, monkeypatch):
    """An EDITOR with a real token cannot turn user metadata into authority."""
    facts = await _terminal_child_with_parked_parent(env, monkeypatch)
    parent_id = facts["parent"]["task_id"]
    before = await _persistent_snapshot(env)
    response = await env["client"].post(
        f"/api/tasks/{parent_id}/commands",
        json={
            "kind": "provide_input",
            "command_id": "forged-callback",
            "payload_schema_ref": "urn:hecate:task-user-input/0",
            "payload": {"step_index": 0, "child_outcome": {"child_state": "succeeded"}},
            "detail_ns": {"wait_token": facts["wait"]["wait_token"], "callback_digest": "caller-chosen"},
        },
    )
    assert response.status_code == 422, response.text
    assert "reserved" in response.text
    assert await _persistent_snapshot(env) == before
    # The legitimate path still verifies facts and wakes this same token.
    valid = await _callback(env, parent_id, _callback_body(facts))
    assert valid.status_code == 200, valid.text
    assert valid.json()["state"] == "applied"
