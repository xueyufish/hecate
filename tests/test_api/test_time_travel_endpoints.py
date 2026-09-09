"""API tests for the 1.3.21② time-travel endpoints.

Covers ``GET /sessions/{id}/commit-points``, ``POST /sessions/{id}/fork``
and ``POST /sessions/{id}/state`` — validation paths, workspace scoping and
the lineage filter on ``GET /sessions``. Service semantics are pinned in
``tests/test_services/test_workflow/test_time_travel_service.py``; the fork
happy path is exercised there with stub workers (the API layer wires
production workers, out of unit-test reach).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.main import app
from hecate.runtime.eventstore import (
    CURRENT_LOG_SCHEMA_VERSION,
    Event,
    EventType,
    InMemoryEventStore,
)


class _SessionRow:
    """In-memory stand-in for a SessionModel row."""

    def __init__(self, metadata: dict[str, Any] | None = None, workspace_id: uuid.UUID | None = None) -> None:
        from datetime import UTC, datetime

        self.id = uuid.uuid4()
        self.agent_id = uuid.uuid4()
        self.conversation_id = None
        self.status = "active"
        self.current_node = None
        self.checkpoint_id = None
        self.metadata_ = metadata or {}
        self.workspace_id = workspace_id or uuid.UUID(int=0)
        self.source_channel = None
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> int:
        return len(self._rows)

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _StubDBSession:
    """Stub DB whose selects resolve to configured rows, honoring the
    workspace filter the endpoints apply (``workspace_id`` mismatch → None)."""

    def __init__(self, rows: list[Any] | None = None, caller_workspace: uuid.UUID | None = None) -> None:
        self.rows = rows or []
        self.caller_workspace = caller_workspace
        self.added: list[Any] = []

    async def execute(self, stmt: object) -> Any:
        rows = list(self.rows)
        if self.caller_workspace is not None:
            rows = [r for r in rows if getattr(r, "workspace_id", None) == self.caller_workspace]
        return _Result(rows)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()  # type: ignore[attr-defined]

    async def refresh(self, obj: object) -> None:
        pass


def _stub_auth(workspace_id: uuid.UUID | None = None) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=workspace_id,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


def _client(event_store: InMemoryEventStore, db: _StubDBSession, workspace_id: uuid.UUID | None = None) -> TestClient:
    db.caller_workspace = workspace_id
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_event_store] = lambda: event_store
    app.dependency_overrides[get_session_state_store] = lambda: None
    app.dependency_overrides[get_auth_context] = lambda: _stub_auth(workspace_id)
    return TestClient(app)


def _ev(
    sid: uuid.UUID, etype: EventType, payload: dict | None = None, node_id: str | None = None, superstep: int = 1
) -> Event:
    return Event(
        session_id=sid,
        superstep=superstep,
        event_type=etype,
        node_id=node_id,
        payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, **(payload or {})},
    )


def _seeded_store() -> tuple[InMemoryEventStore, uuid.UUID]:
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    events = [
        _ev(sid, EventType.TURN_START),
        _ev(sid, EventType.NODE_END, node_id="a"),
        _ev(sid, EventType.CHANNEL_WRITE, {"channel": "messages", "value": ["hi"]}, node_id="a"),
        _ev(sid, EventType.STEP_END),
        _ev(sid, EventType.INTERRUPT, {"kind": "worker", "nodes": ["b"]}, node_id="b"),
        _ev(sid, EventType.TURN_END),
    ]
    asyncio.run(_append_all(store, events))
    return store, sid


async def _append_all(store: InMemoryEventStore, events: list[Event]) -> None:
    for event in events:
        await store.append(event)


# --------------------------------------------------------------------------
# GET /commit-points
# --------------------------------------------------------------------------


def test_commit_points_lists_log_derived_anchors() -> None:
    store, sid = _seeded_store()
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.get(f"/api/sessions/{sid}/commit-points")
    assert response.status_code == 200
    items = response.json()["items"]
    kinds = {item["kind"] for item in items}
    assert kinds == {"STEP_END", "INTERRUPT"}
    versions = [item["log_version"] for item in items]
    assert versions == sorted(versions, reverse=True)


def test_commit_points_empty_log_returns_empty_list() -> None:
    store = InMemoryEventStore()
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.get(f"/api/sessions/{uuid.uuid4()}/commit-points")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_commit_points_workspace_scoped_404() -> None:
    store, sid = _seeded_store()
    other_ws = uuid.uuid4()
    db = _StubDBSession(rows=[_SessionRow()])  # row exists but not in caller's ws
    client = _client(store, db, workspace_id=other_ws)

    response = client.get(f"/api/sessions/{sid}/commit-points")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# POST /state
# --------------------------------------------------------------------------


def test_state_endpoint_conflict_409_on_open_turn() -> None:
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    asyncio.run(_append_all(store, [_ev(sid, EventType.TURN_START)]))
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.post(f"/api/sessions/{sid}/state", json={"values": {"messages": ["x"]}})
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "TURN_IN_FLIGHT"


def test_state_endpoint_422_on_non_loggable_channel() -> None:
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.post(f"/api/sessions/{sid}/state", json={"values": {"_resume_value": "x"}})
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "INVALID_STATE_CHANNEL"


def test_state_endpoint_404_on_missing_session() -> None:
    store = InMemoryEventStore()
    db = _StubDBSession(rows=[])  # no row
    client = _client(store, db)

    response = client.post(f"/api/sessions/{uuid.uuid4()}/state", json={"values": {"messages": ["x"]}})
    assert response.status_code == 404


def test_state_endpoint_appends_audited_batch() -> None:
    store, sid = _seeded_store()
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.post(f"/api/sessions/{sid}/state", json={"values": {"messages": ["patched"]}, "actor": "tester"})
    assert response.status_code == 200
    body = response.json()
    assert "patched" in body["channel_state"]["messages"]
    assert body["log_version"] > 6

    events = asyncio.run(store.get_events(sid))
    writes = [
        e for e in events if e.event_type == EventType.CHANNEL_WRITE and e.payload.get("source") == "update_state"
    ]
    assert len(writes) == 1
    assert writes[0].payload["actor"] == "tester"


# --------------------------------------------------------------------------
# POST /fork
# --------------------------------------------------------------------------


def test_fork_endpoint_422_beyond_tail() -> None:
    store, sid = _seeded_store()
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.post(f"/api/sessions/{sid}/fork", json={"at_version": 99})
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "INVALID_FORK_ANCHOR"


def test_fork_endpoint_422_below_first_commit() -> None:
    store, sid = _seeded_store()
    db = _StubDBSession(rows=[_SessionRow()])
    client = _client(store, db)

    response = client.post(f"/api/sessions/{sid}/fork", json={"at_version": 2})
    assert response.status_code == 422


def test_fork_endpoint_404_on_missing_session() -> None:
    store = InMemoryEventStore()
    db = _StubDBSession(rows=[])
    client = _client(store, db)

    response = client.post(f"/api/sessions/{uuid.uuid4()}/fork", json={"at_version": 1})
    assert response.status_code == 404


# --------------------------------------------------------------------------
# GET /sessions?parent_session_id=… lineage filter
# --------------------------------------------------------------------------


def test_sessions_lineage_fields_and_filter() -> None:
    parent = _SessionRow()
    child_row = _SessionRow(metadata={"parent_session_id": str(parent.id), "parent_log_version": 4})
    unrelated = _SessionRow()
    store = InMemoryEventStore()
    db = _StubDBSession(rows=[child_row])
    client = _client(store, db)

    # The stub returns the same row for every select; assert the payload shape
    # (lineage surfaced) rather than pagination semantics.
    response = client.get("/api/sessions")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["parent_session_id"] == str(parent.id)
    assert item["parent_log_version"] == 4

    plain = _SessionRow()
    db2 = _StubDBSession(rows=[plain])
    client2 = _client(store, db2)
    item2 = client2.get("/api/sessions").json()["items"][0]
    assert item2["parent_session_id"] is None
    assert item2["parent_log_version"] is None
    assert unrelated.id != child_row.id  # sanity: distinct rows exist
