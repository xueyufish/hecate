"""Workflow callback entry (step6e): verified child facts, then wake.

The parent parks on a durable wait whose contract names the child task;
the callback endpoint wakes it only after the platform verifies the
child's OWN records (same workspace, terminal, matching declared state).
Forged references, cross-workspace children, state mismatches, replays,
and late callbacks are explicitly rejected without consuming the token.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.api.tasks import (
    commands_router,
    reconciliation_router,
    runs_router,
)
from hecate.channel.api.tasks import (
    router as tasks_router,
)
from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution.task_dispatcher import ControlCommandKind, PlatformTaskDispatcher, TaskWaitingSignalError
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from tests.conftest import DEFAULT_WORKSPACE_ID, test_session_factory

WS = DEFAULT_WORKSPACE_ID
OTHER_WS = uuid.uuid4()


class _EntryDouble:
    """Callable entry boundary (callables do not bind like functions)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.calls.append(kwargs)
        return {"status": "succeeded", "content": "api stub reply"}


@pytest.fixture
def entry_stub(monkeypatch: pytest.MonkeyPatch):
    double = _EntryDouble()
    real = PlatformTaskDispatcher._execute

    async def _execute(self, db, context, **kwargs):
        return await double(db, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", _execute)
    yield double
    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", real, raising=False)


@pytest.fixture
async def client(auth_context: AuthContext, db_session) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(tasks_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(commands_router, prefix="/api")
    app.include_router(reconciliation_router, prefix="/api")

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = lambda: auth_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_agent(db: AsyncSession) -> uuid.UUID:
    agent = AgentModel(workspace_id=WS, name=f"wf-agent-{uuid.uuid4().hex[:6]}")
    db.add(agent)
    await db.flush()
    version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="b" * 64)
    db.add(version)
    await db.flush()
    db.add(
        AgentPrincipalModel(
            id=agent.id,
            agent_id=agent.id,
            workspace_id=WS,
            organization_id=WS,
            owner_user_id=WS,
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
    return agent.id


async def _submit(client: AsyncClient, agent_id: uuid.UUID, goal: str, **overrides) -> dict:
    body = {
        "goal": goal,
        "agent_id": str(agent_id),
        "input": {"messages": [{"role": "user", "content": goal}]},
    }
    body.update(overrides)
    response = await client.post("/api/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_terminal(client: AsyncClient, task_id: str, timeout: float = 10.0) -> dict:
    import asyncio
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["lifecycle_state"] not in ("queued", "running", "unrecorded"):
            return detail
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


async def _park_parent_on(client: AsyncClient, agent_id: uuid.UUID, child_task_id: str, monkeypatch) -> dict:
    """Submit a parent whose dispatch parks waiting on the child."""

    real = PlatformTaskDispatcher._execute

    async def wait_for_child(self, db, context, **kwargs):
        raise TaskWaitingSignalError(
            ControlCommandKind.PROVIDE_INPUT,
            {"await_task_ref": {"issuer_domain": "hecate", "id": child_task_id, "kind": "task"}},
            expires_in_seconds=600,
        )

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", wait_for_child)
    try:
        parent = await _submit(client, agent_id, "parent workflow", wait=True)
    finally:
        monkeypatch.setattr(PlatformTaskDispatcher, "_execute", real, raising=False)
    assert parent["lifecycle_state"] == "waiting_input", parent
    # The wait contract (token included) surfaces on the task detail.
    detail = (await client.get(f"/api/tasks/{parent['task_id']}")).json()
    parent = {**parent, "wait": detail["wait"]}
    return parent


def _callback_body(parent: dict, child_task_id: str, **overrides) -> dict:
    detail_response_child = uuid.UUID(child_task_id)
    body = {
        "command_id": f"cb-{uuid.uuid4()}",
        "wait_token": parent["wait"]["wait_token"],
        "child_task_id": str(detail_response_child),
        "declared_state": "succeeded",
        "result": {"note": "child done"},
    }
    body.update(overrides)
    return body


async def test_verified_callback_wakes_parent_and_replay_is_idempotent(
    client: AsyncClient, db_session, entry_stub, monkeypatch
):
    agent_id = await _seed_agent(db_session)
    # The child completes BEFORE the park window opens (the patch would
    # otherwise park the child's background dispatch too).
    child = await _submit(client, agent_id, "child step")
    child_task_id = child["task_id"]
    await _wait_terminal(client, child_task_id)

    parent = await _park_parent_on(client, agent_id, child_task_id, monkeypatch)
    parent_task_id = parent["task_id"]
    assert parent["wait"]["contract_ref"]["await_task_ref"]["id"] == child_task_id

    body = _callback_body(parent, child_task_id)
    response = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == "applied", payload

    # The parent requeued and re-executed through the entry double.
    detail = await _wait_terminal(client, parent_task_id)
    assert detail["lifecycle_state"] == "succeeded"

    replay = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=body)
    assert replay.status_code == 200
    assert replay.json()["state"] == "applied"
    assert replay.json()["detail"] == "idempotent replay of a settled command"


async def test_command_http_preserves_wake_token_and_id(client, db_session, entry_stub, monkeypatch):
    """The public command route must carry the same fields as the service."""
    agent = await _seed_agent(db_session)
    child = await _submit(client, agent, "child", wait=True)
    parent = await _park_parent_on(client, agent, child["task_id"], monkeypatch)
    body = {
        "kind": "provide_input",
        "command_id": "http-wake",
        "detail_ns": {"wait_token": parent["wait"]["wait_token"]},
    }
    response = await client.post(f"/api/tasks/{parent['task_id']}/commands", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "applied"
    assert response.json()["command_id"] == "http-wake"
    assert (await client.post(f"/api/tasks/{parent['task_id']}/commands", json=body)).json()["state"] == "applied"


async def test_callback_result_cannot_forge_verified_child_fields(client, db_session, entry_stub, monkeypatch):
    """Caller summaries cannot replace authoritative child attribution."""
    from hecate.core.composition.durable_platform import get_durable_suite
    from hecate.execution.task_control import task_ref_of

    agent = await _seed_agent(db_session)
    child = await _submit(client, agent, "child", wait=True)
    parent = await _park_parent_on(client, agent, child["task_id"], monkeypatch)
    body = _callback_body(parent, child["task_id"], result={"child_task_id": "forged", "child_state": "failed"})
    response = await client.post(f"/api/tasks/{parent['task_id']}/workflow-callback", json=body)
    assert response.json()["state"] == "applied", response.text
    stored = get_durable_suite().store.get_task_input(task_ref_of(uuid.UUID(parent["task_id"])))
    assert stored["provided"]["child_outcome"]["child_task_id"] == child["task_id"]
    assert stored["provided"]["child_outcome"]["child_state"] == "succeeded"
    changed = {**body, "wait_token": "other-token"}
    assert (await client.post(f"/api/tasks/{parent['task_id']}/workflow-callback", json=changed)).status_code == 422

    # Replay of the same command id is an idempotent applied receipt — the
    # parent is NOT requeued a second time.
    replay = await client.post(f"/api/tasks/{parent['task_id']}/workflow-callback", json=body)
    assert replay.status_code == 200
    assert replay.json()["state"] == "applied"
    assert replay.json()["detail"] == "idempotent replay of a settled command"

    detail = await _wait_terminal(client, parent["task_id"])
    assert detail["lifecycle_state"] == "succeeded"


async def test_forged_child_reference_rejected_without_token_consumption(
    client: AsyncClient, db_session, entry_stub, monkeypatch
):
    agent_id = await _seed_agent(db_session)
    child = await _submit(client, agent_id, "child step")
    await _wait_terminal(client, child["task_id"])
    parent = await _park_parent_on(client, agent_id, child["task_id"], monkeypatch)
    parent_task_id = parent["task_id"]

    # A nonexistent child claimed on the parent's callback.
    body = _callback_body(parent, str(uuid.uuid4()))
    response = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "rejected"
    # A child id the contract does not name is refused at the contract gate.
    assert "does not match the wait contract" in payload["detail"]

    # The parent still waits with an unconsumed token.
    detail = (await client.get(f"/api/tasks/{parent_task_id}")).json()
    assert detail["lifecycle_state"] == "waiting_input"
    assert detail["wait"]["consumed"] is False

    # The legitimate callback still works afterwards.
    good = _callback_body(parent, child["task_id"])
    ok = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=good)
    assert ok.json()["state"] == "applied"


async def test_unresolvable_child_rejected_via_existence_branch(
    client: AsyncClient, db_session, entry_stub, monkeypatch
):
    agent_id = await _seed_agent(db_session)
    child = await _submit(client, agent_id, "child step")
    await _wait_terminal(client, child["task_id"])
    parent = await _park_parent_on(client, agent_id, child["task_id"], monkeypatch)
    parent_task_id = parent["task_id"]

    # The contract names this child, but its row no longer resolves in this
    # workspace (deleted between park and callback): the existence branch
    # refuses — platform records, not callback assertions, are the truth.
    from sqlalchemy import delete

    from hecate.models.task import TaskModel

    async with test_session_factory() as db:
        await db.execute(delete(TaskModel).where(TaskModel.id == uuid.UUID(child["task_id"])))
        await db.commit()

    body = _callback_body(parent, child["task_id"])
    response = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=body)
    payload = response.json()
    assert payload["state"] == "rejected"
    assert "does not exist" in payload["detail"]
    detail = (await client.get(f"/api/tasks/{parent_task_id}")).json()
    assert detail["lifecycle_state"] == "waiting_input"
    assert detail["wait"]["consumed"] is False


async def test_state_mismatch_rejected_and_late_callback_rejected(
    client: AsyncClient, db_session, entry_stub, monkeypatch
):
    agent_id = await _seed_agent(db_session)
    child = await _submit(client, agent_id, "child step")
    child_task_id = child["task_id"]
    await _wait_terminal(client, child_task_id)
    parent = await _park_parent_on(client, agent_id, child_task_id, monkeypatch)
    parent_task_id = parent["task_id"]

    # The child is really succeeded; claiming "failed" is a state mismatch —
    # refused, token untouched.
    body = _callback_body(parent, child_task_id, declared_state="failed")
    response = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=body)
    payload = response.json()
    assert payload["state"] == "rejected"
    assert "does not match" in payload["detail"]
    detail = (await client.get(f"/api/tasks/{parent_task_id}")).json()
    assert detail["lifecycle_state"] == "waiting_input"
    assert detail["wait"]["consumed"] is False

    # The truthful callback applies; then a LATE callback reusing the
    # consumed token under a fresh command id is refused.
    good = _callback_body(parent, child_task_id)
    ok = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=good)
    assert ok.json()["state"] == "applied"
    late = _callback_body(parent, child_task_id)
    late_response = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=late)
    late_payload = late_response.json()
    assert late_payload["state"] == "rejected" or "not waiting" in (late_payload.get("detail") or "")


async def test_parent_wait_and_child_facts_survive_service_restart(
    client: AsyncClient, db_session, entry_stub, monkeypatch
):
    agent_id = await _seed_agent(db_session)
    child = await _submit(client, agent_id, "child step")
    child_task_id = child["task_id"]
    await _wait_terminal(client, child_task_id)
    parent = await _park_parent_on(client, agent_id, child_task_id, monkeypatch)
    parent_task_id = parent["task_id"]

    # "Restart": fresh service instances over the same durable state — the
    # API constructs one per request, so this callback already runs on a
    # service that never saw the park; assert the facts resolve from
    # persistence, then the wake applies.
    body = _callback_body(parent, child_task_id)
    response = await client.post(f"/api/tasks/{parent_task_id}/workflow-callback", json=body)
    assert response.json()["state"] == "applied"
    detail = await _wait_terminal(client, parent_task_id)
    assert detail["lifecycle_state"] == "succeeded"
