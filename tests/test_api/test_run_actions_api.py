"""Exercise run-action routes with real registry records and SQL ledger queries."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi import FastAPI
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from httpx import ASGITransport, AsyncClient

from hecate.channel.api.tasks import get_task_control_service, runs_router
from hecate.contracts.execution.durable import ActionIntent, ActionOutcome, ActionOutcomeRecord
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, task_ref
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.stub_durable import (
    InMemoryActionLedger,
    InMemoryControlCommandRecorder,
    InMemoryDurableTaskStore,
)
from hecate.execution.task_control import TaskControlValidationError
from hecate.execution.task_dispatcher import run_row_ref
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from tests.conftest import DEFAULT_WORKSPACE_ID


@pytest.fixture
async def api_records(db_session, auth_context, tmp_path, monkeypatch):
    """Keep the production service dependency and substitute only its resources."""
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="run-actions-http")
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
            workload_id="hecate:query-test",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="observe actions",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    runs = []
    for _ in range(2):
        runs.append(
            await registry.create_run(
                task_id=task.id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
            )
        )
    ledger = SqlDurableStore(f"sqlite:///{tmp_path / 'http-ledger.db'}", source="platform")
    ledger.create_schema()
    binding = {
        "suite": DurableSuite(
            store=ledger,
            recorder=ledger,
            ledger=ledger,
            backend="postgres",
            ledger_source="core",
        ),
        "auth": auth_context,
    }
    monkeypatch.setattr("hecate.channel.api.tasks.get_durable_suite", lambda: binding["suite"])
    app = FastAPI()
    app.include_router(runs_router, prefix="/api")

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
                "runs": runs,
                "ledger": ledger,
            }
    finally:
        app.dependency_overrides.clear()
        ledger.dispose()


def _record(records, run, key):
    ledger = records["ledger"]
    ledger.record_intent_ex(
        ActionIntent(key, f"tool.{key}", "argument-digest", ToolSideEffectClass.NON_IDEMPOTENT_WRITE),
        task_ref=task_ref("hecate", str(records["task"].id)),
        run_ref=run_row_ref(run),
        execution_id=f"exec-{key}",
        tool_call_id=f"tc-{key}",
        session_id="session-sensitive",
    )
    claim, token = ledger.claim_ex(key, holder="http-test")
    assert claim.claimed
    ledger.record_outcome_ex(
        ActionOutcomeRecord(key, ActionOutcome.SUCCEEDED, result_digest=f"digest-{key}"),
        claim_token=token,
        result_payload={"api_key": "sk-PRIVATE", "internal": "full result"},
    )


async def test_sql_history_and_whitelist_through_real_service_dependency(api_records):
    records = api_records
    old, latest = records["runs"]
    _record(records, old, "historical-action")
    _record(records, latest, "latest-action")
    before = records["ledger"].list_run_actions(run_row_ref(old))
    response = await records["client"].get(f"/api/runs/{old.id}/actions?workspace_id={uuid.uuid4()}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"run_id", "task_id", "actions", "ledger_source", "supported"}
    assert body["run_id"] == str(old.id)
    assert body["task_id"] == str(records["task"].id)
    assert body["supported"] is True
    assert body["ledger_source"] == "core"
    assert [action["action_key"] for action in body["actions"]] == ["historical-action"]
    action = body["actions"][0]
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
    assert action["outcome"] == "succeeded"
    assert action["result_digest"] == "digest-historical-action"
    for secret in ("sk-PRIVATE", "full result", "session-sensitive", "result_payload", "claim_token"):
        assert secret not in response.text
    assert records["ledger"].list_run_actions(run_row_ref(old)) == before
    latest_response = await records["client"].get(f"/api/runs/{latest.id}/actions")
    assert [action["action_key"] for action in latest_response.json()["actions"]] == ["latest-action"]
    assert "/api/runs/{run_id}/actions" in records["app"].openapi()["paths"]


@pytest.mark.parametrize("case", ["missing_run", "foreign_workspace", "deleted_run", "deleted_task", "foreign_task"])
async def test_http_denials_are_404_before_ledger_access(api_records, monkeypatch, case):
    records = api_records
    run = records["runs"][0]
    query_id = run.id
    if case == "missing_run":
        query_id = uuid.uuid4()
    elif case == "foreign_workspace":
        records["binding"]["auth"] = replace(records["binding"]["auth"], workspace_id=uuid.uuid4())
    elif case == "deleted_run":
        run.deleted = True
    elif case == "deleted_task":
        records["task"].deleted = True
    elif case == "foreign_task":
        records["task"].workspace_id = uuid.uuid4()
    await records["db"].flush()
    calls = []

    def forbidden_lookup(ref):
        calls.append(ref)
        raise AssertionError("ledger must not be queried")

    monkeypatch.setattr(records["ledger"], "list_run_actions", forbidden_lookup)
    response = await records["client"].get(f"/api/runs/{query_id}/actions")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == {
        "code": "NOT_FOUND",
        "message": f"run {query_id} not found in workspace",
        "details": None,
    }
    assert calls == []


async def test_no_workspace_is_denied_before_ledger_access(api_records, monkeypatch):
    api_records["binding"]["auth"] = replace(api_records["binding"]["auth"], workspace_id=None, role=None)

    def forbidden_lookup(ref):
        raise AssertionError("ledger must not be queried")

    monkeypatch.setattr(api_records["ledger"], "list_run_actions", forbidden_lookup)
    response = await api_records["client"].get(f"/api/runs/{api_records['runs'][0].id}/actions")
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"


async def test_real_empty_and_stub_query_are_distinguished(api_records):
    run = api_records["runs"][0]
    real = await api_records["client"].get(f"/api/runs/{run.id}/actions")
    assert real.status_code == 200
    assert real.json()["supported"] is True
    assert real.json()["actions"] == []
    api_records["binding"]["suite"] = DurableSuite(
        store=InMemoryDurableTaskStore(),
        recorder=InMemoryControlCommandRecorder(),
        ledger=InMemoryActionLedger(),
        backend="stub",
        ledger_source="stub",
    )
    stub = await api_records["client"].get(f"/api/runs/{run.id}/actions")
    assert stub.status_code == 200
    assert stub.json()["supported"] is False
    assert stub.json()["ledger_source"] == "stub"
    assert stub.json()["actions"] == []


@pytest.mark.parametrize("exception_type", [RuntimeError, TaskControlValidationError])
async def test_storage_failure_is_500_without_driver_or_payload_details(
    api_records, monkeypatch, caplog, exception_type
):
    def failing_lookup(ref):
        raise exception_type("postgresql://user:password@host/db SQL parameters: sk-SECRET")

    monkeypatch.setattr(api_records["ledger"], "list_run_actions", failing_lookup)
    run = api_records["runs"][0]
    response = await api_records["client"].get(f"/api/runs/{run.id}/actions")
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == {
        "code": "TASK_CONTROL_ERROR",
        "message": f"action ledger query failed for run {run.id}",
        "details": None,
    }
    for secret in ("password", "sk-SECRET", "SQL parameters"):
        assert secret not in response.text
        assert secret not in caplog.text
    assert str(run.id) in caplog.text
    assert exception_type.__name__ in caplog.text


async def test_invalid_run_id_is_a_validation_error(api_records):
    response = await api_records["client"].get("/api/runs/not-a-uuid/actions")
    assert response.status_code == 422
