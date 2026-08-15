"""Tests for the resume session management endpoint."""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_workspace import get_auth_context
from hecate.engine.eventstore import Event, EventType, InMemoryEventStore
from hecate.main import app


class _StubDBSession:
    """Stub AsyncSession that mimics SQLAlchemy flush/refresh semantics:

    - ``flush`` applies Python-side column defaults (e.g. ``metadata_=dict``).
    - ``refresh`` applies server defaults (``created_at``/``updated_at``).
    A real session does both when persisting a lazily-created SessionModel.
    """

    def __init__(self) -> None:
        self._added: list[object] = []

    async def execute(self, stmt: object) -> object:
        class _Result:
            def scalar_one_or_none(self) -> object | None:
                return None

        return _Result()

    async def flush(self) -> None:
        for obj in self._added:
            if getattr(obj, "metadata_", None) is None:
                obj.metadata_ = {}  # type: ignore[attr-defined]

    async def refresh(self, obj: object) -> None:
        from datetime import UTC, datetime

        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)  # type: ignore[attr-defined]
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime.now(UTC)  # type: ignore[attr-defined]

    def add(self, obj: object) -> None:
        self._added.append(obj)


async def _override_db() -> _StubDBSession:
    return _StubDBSession()


def _stub_auth() -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


def _client(event_store: InMemoryEventStore) -> TestClient:
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_event_store] = lambda: event_store
    app.dependency_overrides[get_auth_context] = _stub_auth
    return TestClient(app)


def test_resume_endpoint_rejects_when_no_unclosed_interrupt() -> None:
    sid = uuid.uuid4()
    client = _client(InMemoryEventStore())
    response = client.post(
        f"/api/sessions/{sid}/resume",
        json={"resume_value": "approve"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"]["code"] == "NOT_INTERRUPTED"


def test_resume_endpoint_accepts_when_unclosed_interrupt_exists() -> None:
    async def setup() -> tuple[InMemoryEventStore, uuid.UUID]:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        await store.append(
            Event(
                session_id=sid,
                superstep=1,
                event_type=EventType.INTERRUPT,
                payload={"interrupt_value_type": "dict"},
            )
        )
        return store, sid

    store, sid = asyncio.run(setup())

    client = _client(store)
    response = client.post(
        f"/api/sessions/{sid}/resume",
        json={"resume_value": "approve"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_resume_endpoint_accepts_when_only_session_row_exists_with_interrupted() -> None:
    """If the Session row already exists with status=interrupted and the log confirms
    an INTERRUPT event, resume proceeds (session row status updates to active).
    """
    from datetime import UTC, datetime

    class _ResultExisting:
        def scalar_one_or_none(self) -> object | None:
            class _Session:
                id = uuid.uuid4()
                agent_id = uuid.uuid4()
                conversation_id = None
                status = "interrupted"
                current_node = None
                checkpoint_id = None
                metadata_ = {}
                workspace_id = uuid.UUID(int=0)
                created_at = datetime.now(UTC)
                updated_at = datetime.now(UTC)
                source_channel = None
                agent = None

            return _Session()

    class _StubDBSessionWithRow(_StubDBSession):
        async def execute(self, stmt: object) -> object:
            return _ResultExisting()

    sid = uuid.uuid4()

    async def setup() -> InMemoryEventStore:
        store = InMemoryEventStore()
        await store.append(
            Event(
                session_id=sid,
                superstep=1,
                event_type=EventType.INTERRUPT,
                payload={"interrupt_value_type": "dict"},
            )
        )
        return store

    store = asyncio.run(setup())

    app.dependency_overrides[get_db] = _StubDBSessionWithRow
    app.dependency_overrides[get_event_store] = lambda: store
    app.dependency_overrides[get_auth_context] = _stub_auth
    client = TestClient(app)
    response = client.post(
        f"/api/sessions/{sid}/resume",
        json={"resume_value": "approve"},
    )
    assert response.status_code == 200
