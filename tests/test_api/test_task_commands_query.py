"""Exercise the task-command list route with real records and SQL queries."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from httpx import ASGITransport, AsyncClient

from hecate.channel.api.tasks import commands_router, get_task_control_service, router
from hecate.contracts.execution.durable import CommandState, ControlCommandKind, ControlCommandRecord
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
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


@pytest.fixture
async def api_records(db_session, auth_context, tmp_path, monkeypatch):
    """Keep the production service dependency and substitute only its resources."""
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="command-query-http")
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
            workload_id="hecate:command-query",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="query commands",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'http-commands.db'}", source="platform")
    store.create_schema()
    binding = {
        "suite": DurableSuite(
            store=store,
            recorder=store,
            ledger=store,
            backend="postgres",
            ledger_source="core",
        ),
        "auth": auth_context,
    }
    monkeypatch.setattr("hecate.channel.api.tasks.get_durable_suite", lambda: binding["suite"])
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(commands_router, prefix="/api")

    async def shared_db():
        yield db_session

    app.dependency_overrides[get_db] = shared_db
    app.dependency_overrides[get_auth_context] = lambda: binding["auth"]
    assert get_task_control_service not in app.dependency_overrides
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "app": app,
                "db": db_session,
                "binding": binding,
                "task": task,
                "store": store,
            }
    finally:
        app.dependency_overrides.clear()
        store.dispose()


def _seed(records, command_id, state, *, issued_at=None, expires_at=None) -> None:
    now = datetime.now(UTC)
    records["store"].record(
        ControlCommandRecord(
            command_id=command_id,
            kind=ControlCommandKind.CANCEL,
            issuer="user-1",
            task_ref=BackendRef(RefKind.TASK, "hecate", str(records["task"].id)),
            issued_at=issued_at or now.isoformat(),
            expires_at=expires_at,
            state=state,
            extra={"workspace_id": str(DEFAULT_WORKSPACE_ID), "secret_attr": "sk-INTERNAL"},
        )
    )


async def test_list_is_paginated_whitelisted_and_read_only(api_records):
    records = api_records
    now = datetime.now(UTC)
    for index in range(3):
        issued = (now - timedelta(minutes=5 - index)).isoformat()
        _seed(records, f"cmd-{index}", CommandState.REQUESTED, issued_at=issued)
    snapshot_before = records["store"].list_task_commands(
        str(DEFAULT_WORKSPACE_ID),
        BackendRef(RefKind.TASK, "hecate", str(records["task"].id)),
        100,
        0,
    )
    response = await records["client"].get(f"/api/tasks/{records['task'].id}/commands?limit=2&offset=0")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total"}
    assert body["total"] == 3
    assert [item["command_id"] for item in body["items"]] == ["cmd-2", "cmd-1"]
    assert set(body["items"][0]) == COMMAND_WHITELIST
    page2 = await records["client"].get(f"/api/tasks/{records['task'].id}/commands?limit=2&offset=2")
    assert [item["command_id"] for item in page2.json()["items"]] == ["cmd-0"]
    assert page2.json()["total"] == 3
    snapshot_after = records["store"].list_task_commands(
        str(DEFAULT_WORKSPACE_ID),
        BackendRef(RefKind.TASK, "hecate", str(records["task"].id)),
        100,
        0,
    )
    assert snapshot_after == snapshot_before
    for secret in ("secret_attr", "sk-INTERNAL", "payload", "detail_ns", "extra"):
        assert secret not in response.text
    assert "/api/tasks/{task_id}/commands" in records["app"].openapi()["paths"]


async def test_five_states_returned_verbatim_in_order(api_records):
    records = api_records
    now = datetime.now(UTC)
    _seed(records, "cmd-acked", CommandState.REQUESTED, issued_at=(now - timedelta(minutes=50)).isoformat())
    _seed(records, "cmd-applied", CommandState.REQUESTED, issued_at=(now - timedelta(minutes=40)).isoformat())
    _seed(records, "cmd-rejected", CommandState.REQUESTED, issued_at=(now - timedelta(minutes=30)).isoformat())
    _seed(records, "cmd-expired", CommandState.REQUESTED, issued_at=(now - timedelta(minutes=20)).isoformat())
    _seed(records, "cmd-open", CommandState.REQUESTED, issued_at=(now - timedelta(minutes=10)).isoformat())
    store = records["store"]
    store.transition("cmd-acked", CommandState.ACKNOWLEDGED)
    store.transition("cmd-acked", CommandState.APPLIED)
    store.transition("cmd-applied", CommandState.ACKNOWLEDGED)
    store.transition("cmd-rejected", CommandState.REJECTED)
    store.transition("cmd-expired", CommandState.EXPIRED)
    response = await records["client"].get(f"/api/tasks/{records['task'].id}/commands")
    assert response.status_code == 200, response.text
    states = {item["command_id"]: item["state"] for item in response.json()["items"]}
    assert states == {
        "cmd-open": "requested",
        "cmd-expired": "expired",
        "cmd-rejected": "rejected",
        "cmd-applied": "acknowledged",
        "cmd-acked": "applied",
    }
    # A lapsed requested command keeps its recorded state in the list.
    store.record(
        ControlCommandRecord(
            command_id="cmd-lapsed",
            kind=ControlCommandKind.CANCEL,
            issuer="user-1",
            task_ref=BackendRef(RefKind.TASK, "hecate", str(records["task"].id)),
            issued_at=(now - timedelta(days=2)).isoformat(),
            expires_at=(now - timedelta(days=1)).isoformat(),
            state=CommandState.REQUESTED,
            extra={"workspace_id": str(DEFAULT_WORKSPACE_ID)},
        )
    )
    lapsed_view = await records["client"].get(f"/api/tasks/{records['task'].id}/commands")
    by_id = {item["command_id"]: item["state"] for item in lapsed_view.json()["items"]}
    assert by_id["cmd-lapsed"] == "requested"


async def test_single_command_read_still_lazy_expires(api_records):
    records = api_records
    now = datetime.now(UTC)
    _seed(
        records,
        "cmd-single",
        CommandState.REQUESTED,
        issued_at=(now - timedelta(days=2)).isoformat(),
        expires_at=(now - timedelta(days=1)).isoformat(),
    )
    single = await records["client"].get("/api/commands/cmd-single")
    assert single.status_code == 200, single.text
    assert single.json()["state"] == "expired"
    listing = await records["client"].get(f"/api/tasks/{records['task'].id}/commands")
    by_id = {item["command_id"]: item["state"] for item in listing.json()["items"]}
    assert by_id["cmd-single"] == "expired"


@pytest.mark.parametrize("case", ["missing_task", "foreign_workspace", "deleted_task", "moved_task"])
async def test_http_denials_are_404_before_recorder_access(api_records, monkeypatch, case):
    records = api_records
    query_id = records["task"].id
    if case == "missing_task":
        query_id = uuid.uuid4()
    elif case == "foreign_workspace":
        records["binding"]["auth"] = replace(records["binding"]["auth"], workspace_id=uuid.uuid4())
    elif case == "deleted_task":
        records["task"].deleted = True
    elif case == "moved_task":
        records["task"].workspace_id = uuid.uuid4()
    await records["db"].flush()
    calls = []

    def forbidden_lookup(*args):
        calls.append(args)
        raise AssertionError("command store must not be queried")

    monkeypatch.setattr(records["store"], "list_task_commands", forbidden_lookup)
    response = await records["client"].get(f"/api/tasks/{query_id}/commands")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == {
        "code": "NOT_FOUND",
        "message": f"task {query_id} not found in workspace",
        "details": None,
    }
    assert calls == []


async def test_no_workspace_is_denied_before_recorder_access(api_records, monkeypatch):
    api_records["binding"]["auth"] = replace(api_records["binding"]["auth"], workspace_id=None, role=None)

    def forbidden_lookup(*args):
        raise AssertionError("command store must not be queried")

    monkeypatch.setattr(api_records["store"], "list_task_commands", forbidden_lookup)
    response = await api_records["client"].get(f"/api/tasks/{api_records['task'].id}/commands")
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"


async def test_storage_failure_is_500_without_driver_details(api_records, monkeypatch, caplog):
    def failing_lookup(*args):
        raise RuntimeError("postgresql://user:password@host/db SQL parameters: sk-SECRET")

    monkeypatch.setattr(api_records["store"], "list_task_commands", failing_lookup)
    response = await api_records["client"].get(f"/api/tasks/{api_records['task'].id}/commands")
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == {
        "code": "TASK_CONTROL_ERROR",
        "message": f"command list query failed for task {api_records['task'].id}",
        "details": None,
    }
    for secret in ("password", "sk-SECRET", "SQL parameters"):
        assert secret not in response.text
        assert secret not in caplog.text
    assert str(api_records["task"].id) in caplog.text
    assert "RuntimeError" in caplog.text


async def test_invalid_uuid_and_pagination_are_validation_errors(api_records):
    task = api_records["task"]
    assert (await api_records["client"].get("/api/tasks/not-a-uuid/commands")).status_code == 422
    assert (await api_records["client"].get(f"/api/tasks/{task.id}/commands?limit=0")).status_code == 422
    assert (await api_records["client"].get(f"/api/tasks/{task.id}/commands?limit=101")).status_code == 422
    assert (await api_records["client"].get(f"/api/tasks/{task.id}/commands?offset=-1")).status_code == 422


async def test_projection_failure_is_redacted(api_records, monkeypatch, caplog):
    class StubBrokenRecord:
        command_id = "broken"

        @property
        def kind(self):
            """Simulate corrupt stored data failing receipt projection."""
            raise RuntimeError("postgresql://user:password@host/db sk-SECRET")

    monkeypatch.setattr(api_records["store"], "list_task_commands", lambda *args: ([StubBrokenRecord()], 1))
    response = await api_records["client"].get(f"/api/tasks/{api_records['task'].id}/commands")
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == {
        "code": "TASK_CONTROL_ERROR",
        "message": f"command list query failed for task {api_records['task'].id}",
        "details": None,
    }
    for secret in ("password", "sk-SECRET"):
        assert secret not in response.text
        assert secret not in caplog.text
