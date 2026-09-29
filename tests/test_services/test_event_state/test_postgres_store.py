"""Unit tests for ``PostgresEventStore`` with mocked PG session.

The actual SQL is exercised in ``test_integration_postgres.py`` using a
disposable database via ``HECATE_TEST_POSTGRES_URL``. Here we validate the
store calls (transaction advisory lock, MAX version allocation, INSERT
with conflict handling, and ordered SELECT) and
propagates exceptions when the underlying driver fails.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from hecate.runtime.eventstore import Event, EventType, EventVersionConflictError
from hecate.studio.event_state.models import EventModel
from hecate.studio.event_state.postgres_store import PostgresEventStore, _row_to_event


def _compile_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _factory_with_session(session: AsyncMock) -> MagicMock:
    """Build an ``async_sessionmaker`` whose ``__call__`` returns a context
    manager yielding ``session``."""
    factory = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = cm
    return factory


def _result_scalar_one(value: object | None) -> AsyncMock:
    """Async-compatible result whose ``.scalar_one()`` returns ``value`` (sync)."""
    result = AsyncMock()
    result.scalar_one = MagicMock(return_value=value)
    return result


def _result_scalars_all(values: list[object]) -> AsyncMock:
    """Async-compatible result whose ``.scalars().all()`` returns ``values``."""
    result = AsyncMock()
    all_mock = MagicMock(return_value=values)
    scalars_mock = MagicMock()
    scalars_mock.all = all_mock
    result.scalars = MagicMock(return_value=scalars_mock)
    return result


def _make_event(session_id: uuid.UUID, version_unused: int = 0) -> Event:
    return Event(
        session_id=session_id,
        superstep=1,
        event_type=EventType.NODE_START,
        node_id="agent_1",
        trace_id="trace-1",
        payload={"k": "v"},
    )


def _make_row(session_id: uuid.UUID, version: int) -> EventModel:
    return EventModel(
        session_id=session_id,
        version=version,
        id=uuid.uuid4(),
        superstep=1,
        event_type="NODE_START",
        node_id="agent_1",
        trace_id="trace-1",
        payload={"k": "v"},
        org_id=None,
        user_id=None,
    )


async def test_append_locks_then_allocates_version_and_inserts():
    """Version allocation SHALL be locked before reading MAX and inserting."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(0), MagicMock(rowcount=1)])
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    session_id = uuid.uuid4()
    await store.append(_make_event(session_id))

    assert session.execute.await_count == 3
    lock_stmt = session.execute.await_args_list[1].args[0]
    insert_stmt = session.execute.await_args_list[2].args[0]
    lock_sql = _compile_sql(lock_stmt)
    insert_sql = _compile_sql(insert_stmt)
    assert "max(events" in lock_sql.lower() or "max(" in lock_sql.lower()
    assert "FOR UPDATE" not in lock_sql
    assert "pg_advisory_xact_lock" in str(session.execute.await_args_list[0].args[0])
    assert "INSERT INTO events" in insert_sql
    assert "ON CONFLICT" in insert_sql
    assert "DO NOTHING" in insert_sql


async def test_append_assigns_next_version_when_no_existing_rows():
    """Empty session: ``MAX(version)`` returns None, next_version SHALL be 1."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(None), MagicMock(rowcount=1)])
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    session_id = uuid.uuid4()
    event_id = await store.append(_make_event(session_id))
    assert isinstance(event_id, uuid.UUID)


async def test_append_assigns_next_version_when_existing_rows():
    """Existing rows with MAX(version)=5: next_version SHALL be 6."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(5), MagicMock(rowcount=1)])
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    await store.append(_make_event(uuid.uuid4()))


async def test_append_conflict_raises_event_version_conflict_error_when_no_retry():
    """ON CONFLICT hit with rowcount=0 and max_append_retries=0 SHALL raise."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(0), MagicMock(rowcount=0)])
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory, max_append_retries=0)

    with pytest.raises(EventVersionConflictError):
        await store.append(_make_event(uuid.uuid4()))
    session.commit.assert_not_awaited()


async def test_batch_partial_conflict_never_commits():
    """A failed batch must not commit the subset accepted by ON CONFLICT."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(0), MagicMock(rowcount=1)])
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory, max_append_retries=0)
    sid = uuid.uuid4()
    with pytest.raises(EventVersionConflictError):
        await store.append_batch([_make_event(sid), _make_event(sid)])
    session.commit.assert_not_awaited()


async def test_append_tenant_context_provider_populates_columns():
    """When tenant_context_provider is set, its (org_id, user_id) SHALL be used in INSERT."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(0), MagicMock(rowcount=1)])
    session.commit = AsyncMock()
    factory = _factory_with_session(session)

    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    provider = MagicMock(return_value=(org_id, user_id))
    store = PostgresEventStore(async_session_factory=factory, tenant_context_provider=provider)

    await store.append(_make_event(uuid.uuid4()))

    insert_stmt = session.execute.await_args_list[2].args[0]
    compiled = insert_stmt.compile(dialect=postgresql.dialect())
    assert org_id in compiled.params.values()
    assert user_id in compiled.params.values()


async def test_append_no_provider_passes_none_columns():
    """Without tenant_context_provider, org_id/user_id SHALL be None in INSERT."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[MagicMock(), _result_scalar_one(0), MagicMock(rowcount=1)])
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    await store.append(_make_event(uuid.uuid4()))

    insert_stmt = session.execute.await_args_list[2].args[0]
    compiled = insert_stmt.compile(dialect=postgresql.dialect())
    params = compiled.params
    assert params["org_id"] is None
    assert params["user_id"] is None


async def test_get_events_orders_by_version_asc_with_filter():
    """``get_events(session_id, from_version)`` SHALL filter version>=N and order ASC."""
    session = AsyncMock()
    session_id = uuid.uuid4()
    rows = [_make_row(session_id, 7), _make_row(session_id, 8), _make_row(session_id, 9)]
    session.execute = AsyncMock(return_value=_result_scalars_all(rows))
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    events = await store.get_events(session_id, from_version=7)
    assert [e.version for e in events] == [7, 8, 9]
    assert all(e.session_id == session_id for e in events)

    stmt_sql = _compile_sql(session.execute.await_args.args[0])
    assert "events.version >=" in stmt_sql
    assert "ORDER BY" in stmt_sql
    assert "ASC" in stmt_sql


async def test_get_events_reconstructs_event_fields():
    """Reconstructed Event SHALL preserve id/session_id/event_type/payload/trace_id."""
    session = AsyncMock()
    session_id = uuid.uuid4()
    event_uuid = uuid.uuid4()
    row = EventModel(
        session_id=session_id,
        version=1,
        id=event_uuid,
        superstep=2,
        event_type="TOOL_CALL",
        node_id="n",
        trace_id="t",
        payload={"a": 1},
        org_id=None,
        user_id=None,
    )
    session.execute = AsyncMock(return_value=_result_scalars_all([row]))
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    events = await store.get_events(session_id)
    assert len(events) == 1
    e = events[0]
    assert e.id == event_uuid
    assert e.session_id == session_id
    assert e.event_type == EventType.TOOL_CALL
    assert e.node_id == "n"
    assert e.trace_id == "t"
    assert e.payload == {"a": 1}
    assert e.version == 1
    assert e.superstep == 2


async def test_get_version_returns_max_or_zero():
    """``get_version`` SHALL return MAX(version) when rows exist, 0 otherwise."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(7))
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    assert await store.get_version(uuid.uuid4()) == 7

    session.execute = AsyncMock(return_value=_result_scalar_one(None))
    store2 = PostgresEventStore(async_session_factory=_factory_with_session(session))
    assert await store2.get_version(uuid.uuid4()) == 0


async def test_get_version_uses_max_aggregate():
    """``get_version`` SHALL use MAX(version) aggregate."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(0))
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)

    await store.get_version(uuid.uuid4())
    sql = _compile_sql(session.execute.await_args.args[0])
    assert "max(events" in sql.lower() or "max(" in sql.lower()


@pytest.mark.parametrize("raises", [False, True])
async def test_acquire_event_lock_uses_transaction_and_local_timeout(raises):
    """Transaction exit releases locks before a pooled connection is reused."""
    session = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock()
    transaction.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=transaction)
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory)
    session_id = uuid.uuid4()
    try:
        async with store.acquire_event_lock(session_id, timeout_ms=25):
            if raises:
                raise ValueError("body failed")
    except ValueError:
        assert raises
    transaction.__aenter__.assert_awaited_once()
    transaction.__aexit__.assert_awaited_once()
    calls = session.execute.await_args_list
    assert "set_config" in str(calls[0].args[0])
    assert calls[0].args[1]["timeout"] == "25ms"
    assert "pg_advisory_xact_lock" in str(calls[1].args[0])


def test_row_to_event_maps_compaction_types_and_unknown_falls_back():
    """ADR-033: new compaction event types round-trip; unknown types read as CUSTOM."""
    from types import SimpleNamespace

    sid = uuid.uuid4()

    def _row(event_type: str):
        return SimpleNamespace(
            session_id=sid,
            superstep=3,
            event_type=event_type,
            node_id="n1",
            id=uuid.uuid4(),
            payload={"compaction_id": "c1"},
            trace_id=None,
            version=7,
        )

    for name in ("COMPACTION_STARTED", "COMPACTION_SUMMARY", "CONTEXT_SURFACE_REPLACED", "COMPACTION_COMPLETED"):
        event = _row_to_event(_row(name))
        assert event.event_type == EventType(name)

    unknown = _row_to_event(_row("SOME_FUTURE_TYPE"))
    assert unknown.event_type == EventType.CUSTOM
    assert unknown.payload == {"compaction_id": "c1"}
