"""Task control API tests (step6 platform track).

Runs the mounted routes through the shared test client (default stub
durable binding — lifecycle and command receipts ride the InMemory
seams; platform events persist on the request database). The entry
execution boundary is stubbed; route semantics, receipts, and error
mapping run for real.
"""

from __future__ import annotations

import json
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
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from tests.conftest import DEFAULT_WORKSPACE_ID, test_session_factory


class _EntryDouble:
    """Callable entry boundary (callables do not bind like functions)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.calls.append(kwargs)
        return {"status": "succeeded", "content": "api stub reply"}


@pytest.fixture
def entry_stub(monkeypatch: pytest.MonkeyPatch):
    """Deterministic dispatcher entry boundary returning one reply."""
    double = _EntryDouble()
    real = PlatformTaskDispatcher._execute

    async def _execute(self, db, context, **kwargs):
        return await double(db, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", _execute)
    yield double.calls
    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", real, raising=False)


@pytest.fixture
async def client(auth_context: AuthContext) -> AsyncGenerator[AsyncClient, None]:
    """Task-control-only app: the routes under test without the full
    ``hecate.main`` import chain, over the shared test database."""

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
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name=f"api-agent-{uuid.uuid4().hex[:6]}")
    db.add(agent)
    await db.flush()
    version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="b" * 64)
    db.add(version)
    await db.flush()
    db.add(
        AgentPrincipalModel(
            id=agent.id,
            agent_id=agent.id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            organization_id=DEFAULT_WORKSPACE_ID,
            owner_user_id=DEFAULT_WORKSPACE_ID,
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
    return agent.id


def _body(agent_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "goal": "summarize inventory",
        "agent_id": str(agent_id),
        "input": {"messages": [{"role": "user", "content": "summarize"}]},
        "wait": True,
    }
    payload.update(overrides)
    return payload


async def test_submit_returns_persistent_references(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    response = await client.post("/api/tasks", json=_body(agent_id))
    assert response.status_code == 201, response.text
    body = response.json()
    assert uuid.UUID(body["task_id"]) and uuid.UUID(body["run_id"])
    assert body["lifecycle_state"] == "succeeded"
    assert body["result"]["content"] == "api stub reply"


async def test_submit_replay_returns_200_with_original(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    first = await client.post("/api/tasks", json=_body(agent_id, idempotency_key="api-key-1"))
    assert first.status_code == 201
    second = await client.post("/api/tasks", json=_body(agent_id, idempotency_key="api-key-1"))
    assert second.status_code == 200
    assert second.json()["task_id"] == first.json()["task_id"]
    assert second.json()["replayed"] is True
    assert len(entry_stub) == 1


async def test_submit_conflict_409(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    await client.post("/api/tasks", json=_body(agent_id, idempotency_key="api-key-2"))
    conflicting = _body(agent_id, idempotency_key="api-key-2", goal="different goal")
    response = await client.post("/api/tasks", json=conflicting)
    assert response.status_code == 409


async def test_task_detail_and_missing_404(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    created = await client.post("/api/tasks", json=_body(agent_id))
    task_id = created.json()["task_id"]

    detail = await client.get(f"/api/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["lifecycle_state"] == "succeeded"
    assert len(detail.json()["runs"]) == 1

    missing = await client.get(f"/api/tasks/{uuid.uuid4()}")
    assert missing.status_code == 404


async def test_events_pagination_and_cursor_resume(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    created = await client.post("/api/tasks", json=_body(agent_id))
    run_id = created.json()["run_id"]

    # The read model in this app carries the direct run-stream event
    # (terminal marker); governance events arrive via the outbox relay and
    # are covered by test_event_relay_projection.
    first = await client.get(f"/api/runs/{run_id}/events", params={"limit": 100})
    assert first.status_code == 200
    page = first.json()
    schemas = [event["payload_schema_ref"] for event in page["events"]]
    assert schemas[-1] == "hecate.platform.run_terminal/0"

    resumed = await client.get(f"/api/runs/{run_id}/events", params={"limit": 100, "cursor": page["next_cursor"]})
    assert resumed.json()["events"] == []

    foreign = await client.get(f"/api/runs/{uuid.uuid4()}/events")
    assert foreign.status_code == 404


async def test_cancel_endpoint_records_not_applies(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    created = await client.post("/api/tasks", json=_body(agent_id))
    task_id = created.json()["task_id"]

    # Terminal task: the cancel is explicitly rejected, never applied.
    cancel = await client.post(f"/api/tasks/{task_id}/cancel")
    assert cancel.status_code == 200
    body = cancel.json()
    assert body["state"] == "rejected"
    assert "terminal" in body["detail"]

    receipt = await client.get(f"/api/commands/{body['command_id']}")
    assert receipt.status_code == 200
    assert receipt.json()["state"] == "rejected"


async def test_command_requires_payload_schema(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    created = await client.post("/api/tasks", json=_body(agent_id))
    task_id = created.json()["task_id"]
    response = await client.post(
        f"/api/tasks/{task_id}/commands",
        json={"kind": "provide_input", "payload": {"answer": "yes"}},
    )
    assert response.status_code == 422


async def test_reconciliation_endpoint_labels_stub_ledger(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    await client.post("/api/tasks", json=_body(agent_id))
    response = await client.get("/api/reconciliation/pending")
    assert response.status_code == 200
    body = response.json()
    assert body["ledger_source"] == "stub"
    assert body["actions"] == []


async def test_sse_stream_renders_until_terminal(client: AsyncClient, db_session, entry_stub):
    agent_id = await _seed_agent(db_session)
    created = await client.post("/api/tasks", json=_body(agent_id))
    run_id = created.json()["run_id"]

    collected: list[dict] = []
    async with client.stream("GET", f"/api/runs/{run_id}/events/stream") as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                collected.append(json.loads(line[len("data: ") :]))
            if collected and collected[-1].get("payload_schema_ref") == "hecate.platform.run_terminal/0":
                break
    assert collected, "SSE stream produced no events"
    assert collected[-1]["payload_schema_ref"] == "hecate.platform.run_terminal/0"
