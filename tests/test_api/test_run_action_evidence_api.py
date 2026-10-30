"""Exercise the action-evidence route with real registry records and a SQL ledger.

The receipt is a read view over recorded facts. These tests pin the
authorization path (visibility first, then the VIEWER floor), the
whitelist of the response, and the fact that a GET never writes.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi import FastAPI
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_runtime.action_ledger import tool_arguments_digest
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
from hecate.models.workspace_member import WorkspaceRole
from tests.conftest import DEFAULT_WORKSPACE_ID


@pytest.fixture
async def api_records(db_session, auth_context, tmp_path, monkeypatch):
    """Keep the production service dependency and substitute only its resources."""
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="evidence-http")
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
            workload_id="hecate:evidence-test",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="read evidence",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    runs = [
        await registry.create_run(
            task_id=task.id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            deployment_id=deployment.id,
            identity_chain=chain,
            backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
        )
        for _ in range(2)
    ]
    ledger = SqlDurableStore(f"sqlite:///{tmp_path / 'http-evidence.db'}", source="platform")
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


def _keys(value):
    """Collect every JSON key in a response body."""
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(key)
            found |= _keys(child)
    elif isinstance(value, list):
        for child in value:
            found |= _keys(child)
    return found


def _record(records, run, key, *, outcome="succeeded", payload=None):
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
        ActionOutcomeRecord(
            key,
            ActionOutcome(outcome),
            result_digest=tool_arguments_digest(payload) if payload is not None else None,
        ),
        claim_token=token,
        result_payload=payload,
    )
    return key


async def test_receipt_over_real_service_dependency(api_records):
    """The route returns the whitelisted receipt and writes nothing."""
    records = api_records
    old, latest = records["runs"]
    key = _record(records, old, "recorded-action", payload={"rows": 2})
    before = records["ledger"].list_run_actions(run_row_ref(old))

    response = await records["client"].get(f"/api/runs/{old.id}/action-evidence?action_key={key}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"run_id", "task_id", "supported", "ledger_source", "receipt"}
    assert body["supported"] is True
    assert body["ledger_source"] == "core"
    receipt = body["receipt"]
    assert receipt["action_key"] == key
    assert receipt["task_id"] == str(records["task"].id)
    assert receipt["run_id"] == str(old.id)
    assert receipt["outcome"] == "succeeded"
    assert receipt["result_state"] == "recorded"
    assert receipt["result_verification"]["status"] == "verified"
    assert receipt["decision_ref"]["status"] == "not_recorded"
    for secret in ("sk-PRIVATE", "full result", "session-sensitive"):
        assert secret not in response.text
    assert not _keys(body) & {"claim_token", "result_payload", "result_ref", "session_id", "arguments"}
    assert records["ledger"].list_run_actions(run_row_ref(old)) == before
    assert "/api/runs/{run_id}/action-evidence" in records["app"].openapi()["paths"]


async def test_action_key_with_special_characters_round_trips(api_records):
    """The key travels as a query parameter, so any character survives."""
    records = api_records
    run = records["runs"][0]
    key = "act/键 with space & ?#%"
    _record(records, run, key, payload={"ok": True})

    response = await records["client"].get(f"/api/runs/{run.id}/action-evidence", params={"action_key": key})
    assert response.status_code == 200, response.text
    assert response.json()["receipt"]["action_key"] == key


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

    def forbidden_lookup(ref, action_key):
        calls.append((ref, action_key))
        raise AssertionError("ledger must not be queried")

    monkeypatch.setattr(records["ledger"], "get_run_action", forbidden_lookup)
    response = await records["client"].get(f"/api/runs/{query_id}/action-evidence?action_key=anything")
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert calls == []


async def test_no_workspace_is_denied_before_ledger_access(api_records, monkeypatch):
    api_records["binding"]["auth"] = replace(api_records["binding"]["auth"], workspace_id=None, role=None)

    def forbidden_lookup(ref, action_key):
        raise AssertionError("ledger must not be queried")

    monkeypatch.setattr(api_records["ledger"], "get_run_action", forbidden_lookup)
    response = await api_records["client"].get(
        f"/api/runs/{api_records['runs'][0].id}/action-evidence?action_key=anything"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"


async def test_unknown_action_is_404(api_records):
    records = api_records
    run = records["runs"][0]
    response = await records["client"].get(f"/api/runs/{run.id}/action-evidence?action_key=never-existed")
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"


async def test_missing_action_key_is_a_validation_error(api_records):
    response = await api_records["client"].get(f"/api/runs/{api_records['runs'][0].id}/action-evidence")
    assert response.status_code == 422


async def test_invalid_run_id_is_a_validation_error(api_records):
    response = await api_records["client"].get("/api/runs/not-a-uuid/action-evidence?action_key=x")
    assert response.status_code == 422


async def test_stub_ledger_is_reported_as_unsupported(api_records):
    """A store without the query capability is labeled, not silently empty."""
    records = api_records
    run = records["runs"][0]
    records["binding"]["suite"] = DurableSuite(
        store=InMemoryDurableTaskStore(),
        recorder=InMemoryControlCommandRecorder(),
        ledger=InMemoryActionLedger(),
        backend="stub",
        ledger_source="stub",
    )
    response = await records["client"].get(f"/api/runs/{run.id}/action-evidence?action_key=anything")
    assert response.status_code == 200
    assert response.json()["supported"] is False
    assert response.json()["ledger_source"] == "stub"
    assert "receipt" not in response.json()


@pytest.mark.parametrize("exception_type", [RuntimeError, TaskControlValidationError])
async def test_storage_failure_is_500_without_driver_or_payload_details(
    api_records, monkeypatch, caplog, exception_type
):
    def failing_lookup(ref, action_key):
        raise exception_type("postgresql://user:password@host/db SQL parameters: sk-SECRET")

    monkeypatch.setattr(api_records["ledger"], "get_run_action", failing_lookup)
    run = api_records["runs"][0]
    response = await records_client(api_records, run, "anything")
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == {
        "code": "TASK_CONTROL_ERROR",
        "message": f"action evidence query failed for run {run.id}",
        "details": None,
    }
    for secret in ("password", "sk-SECRET", "SQL parameters"):
        assert secret not in response.text
        assert secret not in caplog.text
    assert str(run.id) in caplog.text
    assert exception_type.__name__ in caplog.text


async def records_client(records, run, action_key):
    """Call the evidence route for one action key."""
    return await records["client"].get(f"/api/runs/{run.id}/action-evidence?action_key={action_key}")


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_all_observer_roles_can_read_a_receipt(api_records, role):
    """The existing VIEWER floor applies to the new read surface."""
    records = api_records
    records["binding"]["auth"] = replace(records["binding"]["auth"], role=role)
    run = records["runs"][0]
    key = _record(records, run, "visible", payload={"ok": True})
    response = await records_client(records, run, key)
    assert response.status_code == 200


async def test_revocation_is_rechecked_before_another_ledger_read(api_records, monkeypatch):
    """A previously read receipt grants no future reading authority."""
    records = api_records
    run = records["runs"][0]
    key = _record(records, run, "visible", payload={"ok": True})
    assert (await records_client(records, run, key)).status_code == 200
    records["binding"]["auth"] = replace(records["binding"]["auth"], role=None)

    def forbidden_lookup(*args):
        raise AssertionError("revoked readers cannot access the ledger")

    monkeypatch.setattr(records["ledger"], "get_run_action", forbidden_lookup)
    response = await records["client"].get(
        f"/api/runs/{run.id}/action-evidence", params={"action_key": key, "role": "ADMIN"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
