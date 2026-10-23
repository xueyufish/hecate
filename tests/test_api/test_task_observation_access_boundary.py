"""Task observation authorization with populated SQL and durable records.

Exercise the production dependency and service, switching only authenticated
contexts. Denials precede business queries; successful reads preserve lifecycle,
wait information, workspace isolation, pagination and attempt ordering.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from hecate.channel.api import tasks as tasks_api
from hecate.channel.api.tasks import get_task_control_service, router
from hecate.contracts.execution.durable import (
    ActionIntent,
    ActionOutcome,
    ActionOutcomeRecord,
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyKey,
    TaskLifecycleState,
)
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.governance_events import RUN_TERMINAL, TASK_SUBMITTED, PlatformEventService
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
from hecate.models.workspace_member import WorkspaceRole
from tests.conftest import DEFAULT_WORKSPACE_ID


@pytest.fixture
async def env(db_session, auth_context, tmp_path, monkeypatch):
    """Bind real services to isolated resources and observe every sensitive read."""
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
    org = OrganizationModel(name="Other org", slug="task-observe-other", owner_id=auth_context.user_id)
    db_session.add(org)
    await db_session.flush()
    other = WorkspaceModel(org_id=org.id, name="Other workspace", slug="task-observe-other")
    db_session.add(other)
    await db_session.flush()
    tasks = [task]
    for index in range(4):
        tasks.append(
            await registry.create_task(
                goal=f"task-observe-{index}",
                initiator_ref=chain.to_dict(),
                workspace_id=other.id if index == 3 else DEFAULT_WORKSPACE_ID,
            )
        )
    tasks[3].deleted = True
    for index, row in enumerate(tasks):
        row.created_at = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index)
    runs = [run]
    for _index in range(3):
        runs.append(
            await registry.create_run(
                task_id=task.id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
            )
        )
    runs[1].deleted = True
    runs[3].workspace_id = other.id
    for index, row in enumerate(runs):
        row.created_at = datetime(2020, 1, 1, tzinfo=UTC) - timedelta(days=index)
    run.projection = {"state": "failed"}
    runs[2].projection = {"state": "waiting_input"}
    runs[2].event_cursor = 7
    await db_session.commit()

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'task-observe-boundary.db'}", source="platform")
    store.create_schema()
    monkeypatch.setattr(
        tasks_api,
        "get_durable_suite",
        lambda: DurableSuite(
            store=store,
            recorder=store,
            ledger=store,
            backend="postgres",
            ledger_source="core",
        ),
    )
    records = {
        "db": db_session,
        "store": store,
        "task": task,
        "run": run,
        "tasks": tasks,
        "runs": runs,
        "other": other,
        "admin_ctx": auth_context,
        "holder": {"ctx": auth_context},
        "calls": [],
    }
    await _seed_facts(records)
    wait = {"wake_kind": "provide_input", "wait_token": "existing-wait", "consumed": False}
    task_ref = BackendRef(RefKind.TASK, "hecate", str(task.id))
    store.apply_task_state(task_ref, TaskLifecycleState.RUNNING, expected_revision=0)
    store.apply_task_state(
        task_ref,
        TaskLifecycleState.WAITING_INPUT,
        expected_revision=1,
        extra_update={"wait": wait},
    )
    records["wait"] = wait
    for row in (tasks[1], tasks[3], tasks[4]):
        store.submit_task(
            key=IdempotencyKey(str(row.id), str(auth_context.user_id), str(row.workspace_id), "b" * 64),
            task_ref=BackendRef(RefKind.TASK, "hecate", str(row.id)),
            run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
            input_payload={"goal": row.goal},
        )

    class StubCountingRelay:
        """Detect accidental projection without consuming pending outbox rows."""

        async def pump_once(self) -> None:
            """Record an attempted projection."""
            records["calls"].append("relay")

    monkeypatch.setattr(tasks_api, "_worker_state", SimpleNamespace(durable_outbox_relay=StubCountingRelay()))
    for name in ("get_task_state", "list_tasks", "get_task_input", "list_run_actions", "record", "transition"):
        original = getattr(store, name)

        def observed(*args, _name=name, _original=original, **kwargs):
            records["calls"].append("store." + _name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observed)
    for name in ("list_tasks", "get_task_detail", "_pump_events", "_dispatch_task"):
        original = getattr(TaskControlService, name)

        async def observed_async(self, *args, _name=name, _original=original, **kwargs):
            records["calls"].append("service." + _name)
            return await _original(self, *args, **kwargs)

        monkeypatch.setattr(TaskControlService, name, observed_async)

    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def shared_db():
        yield db_session

    async def swapped_auth():
        return records["holder"]["ctx"]

    app.dependency_overrides[get_db] = shared_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    assert get_task_control_service not in app.dependency_overrides
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            records["client"] = client
            yield records
    finally:
        app.dependency_overrides.clear()
        store.dispose()


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


INVISIBLE = ("foreign_identity", "foreign_task", "missing", "deleted")


@pytest.mark.parametrize("entry", ("", "/runs"))
@pytest.mark.parametrize("case", INVISIBLE)
@pytest.mark.parametrize("role", (None, WorkspaceRole.VIEWER))
async def test_invisible_tasks_reject_before_role_and_business_reads(env, case, entry, role):
    """Both resource routes hide invisible tasks without touching durable facts."""
    task_id = env["task"].id
    ctx = replace(env["admin_ctx"], role=role)
    if case == "foreign_identity":
        ctx = replace(ctx, workspace_id=env["other"].id, org_id=env["other"].org_id)
    elif case == "foreign_task":
        task_id = env["tasks"][4].id
    elif case == "missing":
        task_id = uuid.uuid4()
    elif case == "deleted":
        task_id = env["tasks"][3].id
    env["holder"]["ctx"] = ctx
    before = await _snapshot(env)
    assert before["tasks"] and before["runs"] and before["platform_events"]
    assert before["durable_task_state"] and before["durable_event_log"] and before["durable_command"]
    assert before["durable_action_intent"] and before["durable_action_outcome"]
    response = await env["client"].get(f"/api/tasks/{task_id}{entry}?role=admin")
    assert response.status_code == 404, response.text
    assert response.json() == {
        "detail": {
            "error": {
                "code": "NOT_FOUND",
                "message": f"task {task_id} not found in workspace",
                "details": None,
            }
        }
    }
    assert env["calls"] == []
    assert await _snapshot(env) == before


@pytest.mark.parametrize("entry", ("list", "detail", "runs"))
@pytest.mark.parametrize("case", ("roleless", "no_workspace", "system"))
async def test_forbidden_requests_never_reach_business_or_durable_reads(env, monkeypatch, case, entry):
    """The workspace requirement precedes visibility and role checks."""
    ctx = replace(env["admin_ctx"], role=None)
    if case == "no_workspace":
        ctx = replace(ctx, workspace_id=None)
    elif case == "system":
        ctx = replace(ctx, workspace_id=None, org_id=None, auth_method="api_key", api_key_scope="system")
    env["holder"]["ctx"] = ctx
    if case != "roleless":

        async def unexpected_visibility(*args, **kwargs):
            pytest.fail("workspace-less request reached resource lookup")

        monkeypatch.setattr(TaskControlService, "assert_task_visible", unexpected_visibility)
        await _assert_forbidden(env, entry, "NO_WORKSPACE")
    else:
        await _assert_forbidden(env, entry, "FORBIDDEN")


async def _assert_forbidden(env, entry, code):
    before = await _snapshot(env)
    base = f"/api/tasks/{env['task'].id}"
    url = "/api/tasks" if entry == "list" else base + ("/runs" if entry == "runs" else "")
    response = await env["client"].get(url, params={"role": "admin", "lifecycle_state": "queued"})
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == code
    assert env["calls"] == []
    assert await _snapshot(env) == before


async def _responses(env):
    """Read all variants using the caller's currently selected identity."""
    base = f"/api/tasks/{env['task'].id}"
    urls = {
        "list": "/api/tasks",
        "page": "/api/tasks?page=2&page_size=1",
        "queued": "/api/tasks?lifecycle_state=queued&page_size=1",
        "waiting": "/api/tasks?lifecycle_state=waiting_input",
        "unrecorded": "/api/tasks?lifecycle_state=unrecorded",
        "detail": base,
        "runs": base + "/runs",
        "empty_runs": f"/api/tasks/{env['tasks'][2].id}/runs",
        "unrecorded_detail": f"/api/tasks/{env['tasks'][2].id}",
    }
    result = {}
    for name, url in urls.items():
        response = await env["client"].get(url)
        assert response.status_code == 200, response.text
        result[name] = response.json()
    return result


@pytest.mark.parametrize("role", (WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN))
async def test_authorized_roles_preserve_populated_responses_and_facts(env, role):
    """Every role exercises its own context against nonempty deterministic data."""
    before = await _snapshot(env)
    baseline = await _responses(env)
    selected = replace(env["admin_ctx"], role=role)
    env["holder"]["ctx"] = selected
    actual = await _responses(env)
    assert env["holder"]["ctx"] is selected
    assert actual == baseline
    assert [row["task_id"] for row in actual["list"]["items"]] == [str(t.id) for t in env["tasks"][:3][::-1]]
    assert actual["list"]["total"] == 3
    assert actual["page"]["total"] == 3
    assert [row["task_id"] for row in actual["page"]["items"]] == [str(env["tasks"][1].id)]
    assert actual["queued"]["total"] == 1
    assert [row["task_id"] for row in actual["queued"]["items"]] == [str(env["tasks"][1].id)]
    assert actual["waiting"]["total"] == 1
    assert actual["waiting"]["items"][0]["task_id"] == str(env["task"].id)
    # Preserve the existing unrecorded filter's response, including recorded
    # tasks; this authorization change does not redefine query semantics.
    assert actual["unrecorded"] == actual["list"]
    detail = actual["detail"]
    assert detail["goal"] == env["task"].goal
    assert detail["lifecycle_state"] == "waiting_input"
    assert detail["wait"] == env["wait"]
    assert actual["runs"] == {"items": detail["runs"]}
    assert [row["run_id"] for row in detail["runs"]] == [str(env["runs"][index].id) for index in (0, 2)]
    assert [row["attempt_no"] for row in detail["runs"]] == [1, 3]
    assert detail["runs"][1]["projection"] == {"state": "waiting_input"}
    assert detail["runs"][1]["event_cursor"] == 7
    assert actual["empty_runs"] == {"items": []}
    assert actual["unrecorded_detail"]["lifecycle_state"] == "unrecorded"
    assert "wait" not in actual["unrecorded_detail"]
    assert "service.get_task_detail" in env["calls"] and "store.list_tasks" in env["calls"]
    assert "store.get_task_state" in env["calls"]
    assert "relay" not in env["calls"] and "service._dispatch_task" not in env["calls"]
    assert await _snapshot(env) == before
