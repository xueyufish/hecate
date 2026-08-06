"""Unit tests for ``PostgresSessionStateStore`` with mocked PG session.

The actual SQL is exercised in ``test_integration_postgres.py`` (testcontainers
gated by ``RUN_INTEGRATION_TESTS=1``). Here we validate the ABC contract:
the store calls the expected SQLAlchemy methods (UPSERT, SELECT, ordered
list) and propagates exceptions when the underlying driver fails.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from hecate.engine.session_state import SessionState
from hecate.services.session_state.postgres_store import PostgresSessionStateStore


def _compile_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _factory_with_session(session: AsyncMock) -> AsyncMock:
    """Build an ``async_sessionmaker`` whose ``__call__`` returns a context
    manager yielding ``session``."""
    factory = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = cm
    return factory


def _result_scalar_one_or_none(value: object | None) -> AsyncMock:
    """Build an async-compatible result where ``await session.execute(stmt)``
    returns an object whose ``.scalar_one_or_none()`` is a synchronous method
    (matching SQLAlchemy 2.0 async session semantics)."""
    result = AsyncMock()
    scalar_mock = MagicMock(return_value=value)
    result.scalar_one_or_none = scalar_mock
    return result


def _result_scalars_all(values: list[object]) -> AsyncMock:
    """Build an async-compatible result where ``.scalars().all()`` returns
    the supplied list (sync method chain)."""
    result = AsyncMock()
    all_mock = MagicMock(return_value=values)
    scalars_mock = MagicMock()
    scalars_mock.all = all_mock
    result.scalars = MagicMock(return_value=scalars_mock)
    return result


async def test_pg_save_calls_insert_upsert():
    """``save`` SHALL execute SELECT FOR UPDATE (row lock) then UPSERT."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresSessionStateStore(async_session_factory=factory)

    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    state = SessionState(metadata={"superstep": 5})
    await store.save(org_id, user_id, session_id, state)

    # Two executions: SELECT FOR UPDATE (row lock) + UPSERT.
    assert session.execute.await_count == 2
    lock_stmt = session.execute.await_args_list[0].args[0]
    upsert_stmt = session.execute.await_args_list[1].args[0]
    lock_sql = _compile_sql(lock_stmt)
    upsert_sql = _compile_sql(upsert_stmt)
    assert "FOR UPDATE" in lock_sql, "first execution SHALL lock the row"
    assert "INSERT INTO session_states" in upsert_sql
    assert "ON CONFLICT" in upsert_sql


async def test_pg_save_propagates_driver_exception():
    """PG failure SHALL propagate as a real error (PG is the source of truth)."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=ConnectionError("PG down"))
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresSessionStateStore(async_session_factory=factory)

    with pytest.raises(ConnectionError):
        await store.save(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), SessionState())


async def test_pg_load_returns_none_for_unknown_session():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one_or_none(None))
    factory = _factory_with_session(session)
    store = PostgresSessionStateStore(async_session_factory=factory)

    loaded = await store.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert loaded is None


async def test_pg_load_returns_deserialized_state():
    state = SessionState(channel_state={"k": "v"}, event_position=2)
    row = MagicMock()
    row.state = state.model_dump_json()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one_or_none(row))
    factory = _factory_with_session(session)
    store = PostgresSessionStateStore(async_session_factory=factory)

    loaded = await store.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert loaded is not None
    assert loaded.channel_state == {"k": "v"}


async def test_pg_list_recent_orders_by_updated_at_desc():
    row_a = MagicMock()
    row_a.session_id = uuid.uuid4()
    row_a.org_id = uuid.uuid4()
    row_a.user_id = uuid.uuid4()
    row_a.updated_at = MagicMock()
    row_a.superstep = 1
    row_b = MagicMock()
    row_b.session_id = uuid.uuid4()
    row_b.org_id = row_a.org_id
    row_b.user_id = row_a.user_id
    row_b.updated_at = MagicMock()
    row_b.superstep = 2

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalars_all([row_b, row_a]))
    factory = _factory_with_session(session)
    store = PostgresSessionStateStore(async_session_factory=factory)

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    summaries = await store.list_recent(org_id, user_id)

    assert len(summaries) == 2
    session.execute.assert_called_once()
    stmt = session.execute.await_args.args[0]
    compiled_sql = _compile_sql(stmt)
    assert "ORDER BY" in compiled_sql
    assert "DESC" in compiled_sql
    assert "LIMIT" in compiled_sql
