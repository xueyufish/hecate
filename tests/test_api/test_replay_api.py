"""API tests for 8.20 execution replay endpoints.

Covers:
- GET /api/sessions/{id}/replay (timeline, pagination, payload truncation, 404)
- GET /api/sessions/{id}/replay/state (commit-point fallback, 422 on bad schema)
- Session detail exposes log_version (3.3)
- Tenant scoping on the replay endpoints (3.4 + spec)
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps_event_store import get_event_store
from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType, InMemoryEventStore
from hecate.models.agent import AgentModel
from hecate.models.session import SessionModel


def _make_event(
    session_id: uuid.UUID,
    *,
    version: int,
    superstep: int,
    event_type: EventType,
    payload: dict | None = None,
    trace_id: str | None = "trace-A",
    node_id: str | None = None,
    log_schema_version: int | None = CURRENT_LOG_SCHEMA_VERSION,
) -> Event:
    if payload is None:
        payload = {}
    if log_schema_version is not None:
        payload = {**payload, "log_schema_version": log_schema_version}
    return Event(
        session_id=session_id,
        superstep=superstep,
        event_type=event_type,
        node_id=node_id,
        trace_id=trace_id,
        version=version,
        payload=payload,
    )


async def _create_agent(db: AsyncSession, workspace_id: uuid.UUID) -> AgentModel:
    agent = AgentModel(
        name="replay-test-agent",
        model_config_db={"model": "test"},
        workspace_id=workspace_id,
    )
    db.add(agent)
    await db.flush()
    return agent


async def _create_session(db: AsyncSession, *, agent_id: uuid.UUID, workspace_id: uuid.UUID) -> SessionModel:
    session = SessionModel(agent_id=agent_id, workspace_id=workspace_id, status="completed")
    db.add(session)
    await db.flush()
    return session


@pytest.mark.asyncio
async def test_replay_timeline_returns_partitioned_traces(
    client: AsyncClient, db_session: AsyncSession, default_workspace
) -> None:
    workspace_id = default_workspace.id
    agent = await _create_agent(db_session, workspace_id)
    session = await _create_session(db_session, agent_id=agent.id, workspace_id=workspace_id)

    store = InMemoryEventStore()
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_event_store] = lambda: store

    for i in range(4):
        await store.append(
            _make_event(
                session.id,
                version=i + 1,
                superstep=1,
                event_type=EventType.NODE_START,
                node_id="A",
                trace_id="trace-1" if i < 2 else "trace-2",
            )
        )

    response = await client.get(f"/api/sessions/{session.id}/replay")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["traces"]) == 2
    assert [seg["trace_id"] for seg in payload["traces"]] == ["trace-1", "trace-2"]


@pytest.mark.asyncio
async def test_replay_timeline_404_for_missing_session(client: AsyncClient) -> None:
    fake_id = uuid.uuid4()
    response = await client.get(f"/api/sessions/{fake_id}/replay")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_replay_timeline_limit_caps_response(
    client: AsyncClient, db_session: AsyncSession, default_workspace
) -> None:
    workspace_id = default_workspace.id
    agent = await _create_agent(db_session, workspace_id)
    session = await _create_session(db_session, agent_id=agent.id, workspace_id=workspace_id)

    store = InMemoryEventStore()
    client._transport.app.dependency_overrides[get_event_store] = lambda: store  # type: ignore[attr-defined]
    for i in range(10):
        await store.append(
            _make_event(session.id, version=i + 1, superstep=1, event_type=EventType.CUSTOM, trace_id="t")
        )

    response = await client.get(f"/api/sessions/{session.id}/replay?limit=4")
    assert response.status_code == 200
    body = response.json()
    total_events = sum(seg["event_count"] for seg in body["traces"]) + len(body["unattributed"])
    assert total_events == 4
    assert body["next_cursor"] == 5


@pytest.mark.asyncio
async def test_replay_timeline_payload_truncation_flag(
    client: AsyncClient, db_session: AsyncSession, default_workspace
) -> None:
    workspace_id = default_workspace.id
    agent = await _create_agent(db_session, workspace_id)
    session = await _create_session(db_session, agent_id=agent.id, workspace_id=workspace_id)
    store = InMemoryEventStore()
    client._transport.app.dependency_overrides[get_event_store] = lambda: store  # type: ignore[attr-defined]
    await store.append(
        _make_event(
            session.id,
            version=1,
            superstep=1,
            event_type=EventType.LLM_REQUEST,
            payload={"messages": ["x" * 600]},
        )
    )
    response = await client.get(f"/api/sessions/{session.id}/replay")
    body = response.json()
    assert body["payload_truncated"] is True
    assert body["payload_preview_chars"] == 200


@pytest.mark.asyncio
async def test_replay_state_falls_back_to_commit_point(
    client: AsyncClient, db_session: AsyncSession, default_workspace
) -> None:
    workspace_id = default_workspace.id
    agent = await _create_agent(db_session, workspace_id)
    session = await _create_session(db_session, agent_id=agent.id, workspace_id=workspace_id)
    store = InMemoryEventStore()
    client._transport.app.dependency_overrides[get_event_store] = lambda: store  # type: ignore[attr-defined]
    for v, et in [(1, EventType.CHANNEL_WRITE), (2, EventType.STEP_END), (3, EventType.STEP_END)]:
        await store.append(
            _make_event(
                session.id,
                version=v,
                superstep=1,
                event_type=et,
                payload=(
                    {"channel": "messages", "value": [{"role": "user", "content": "hi"}]}
                    if et == EventType.CHANNEL_WRITE
                    else {}
                ),
            )
        )
    response = await client.get(f"/api/sessions/{session.id}/replay/state?at_version=2")
    assert response.status_code == 200
    body = response.json()
    assert body["effective_version"] == 2
    assert body["fell_back"] is False


@pytest.mark.asyncio
async def test_replay_state_422_for_non_replayable_prefix(
    client: AsyncClient, db_session: AsyncSession, default_workspace
) -> None:
    workspace_id = default_workspace.id
    agent = await _create_agent(db_session, workspace_id)
    session = await _create_session(db_session, agent_id=agent.id, workspace_id=workspace_id)
    store = InMemoryEventStore()
    client._transport.app.dependency_overrides[get_event_store] = lambda: store  # type: ignore[attr-defined]
    await store.append(
        _make_event(
            session.id,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [{"role": "user", "content": "x"}]},
            log_schema_version=1,  # below CURRENT
        )
    )
    await store.append(
        _make_event(
            session.id,
            version=2,
            superstep=1,
            event_type=EventType.STEP_END,
            log_schema_version=1,
        )
    )
    response = await client.get(f"/api/sessions/{session.id}/replay/state?at_version=2")
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "NON_REPLAYABLE_PREFIX"


@pytest.mark.asyncio
async def test_session_detail_exposes_log_version(
    client: AsyncClient, db_session: AsyncSession, default_workspace
) -> None:
    workspace_id = default_workspace.id
    agent = await _create_agent(db_session, workspace_id)
    session = await _create_session(db_session, agent_id=agent.id, workspace_id=workspace_id)

    store = InMemoryEventStore()
    client._transport.app.dependency_overrides[get_event_store] = lambda: store  # type: ignore[attr-defined]
    response = await client.get(f"/api/sessions/{session.id}")
    assert response.status_code == 200
    assert response.json()["log_version"] == 0

    await store.append(_make_event(session.id, version=1, superstep=1, event_type=EventType.CUSTOM))
    response = await client.get(f"/api/sessions/{session.id}")
    assert response.json()["log_version"] == 1


@pytest.mark.asyncio
async def test_replay_timeline_cross_workspace_404(client: AsyncClient, db_session: AsyncSession) -> None:
    """A session in another workspace must return 404 (not 403) to avoid existence leaks."""
    from hecate.models.organization import OrganizationModel
    from hecate.models.workspace import WorkspaceModel

    other_org = OrganizationModel(name="Other Org", slug="other", owner_id=uuid.UUID(int=1))
    db_session.add(other_org)
    await db_session.flush()
    other_ws = WorkspaceModel(name="other", slug="other", org_id=other_org.id)
    db_session.add(other_ws)
    await db_session.flush()
    other_agent = await _create_agent(db_session, other_ws.id)
    other_session = await _create_session(db_session, agent_id=other_agent.id, workspace_id=other_ws.id)

    response = await client.get(f"/api/sessions/{other_session.id}/replay")
    assert response.status_code == 404
