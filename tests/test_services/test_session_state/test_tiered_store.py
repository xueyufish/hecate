"""Unit tests for ``TieredSessionStateStore`` coordinating Redis + Postgres.

Validates the write-through + read-through + cache-fallback protocols.

The ``RedisSessionStateStore`` and ``PostgresSessionStateStore`` are mocked at
their public-method boundary (``AsyncMock`` with awaited save/load/list_recent)
rather than at the underlying client level — the unit under test is
``TieredSessionStateStore``'s coordination logic, not its dependencies'
serialization details (those have separate unit tests).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from hecate.runtime.session_state import SessionState, SessionSummary
from hecate.studio.session_state.tiered_store import TieredSessionStateStore


def _pg_store_mock() -> AsyncMock:
    pg = AsyncMock()
    pg.save = AsyncMock()
    pg.load = AsyncMock(return_value=None)
    pg.list_recent = AsyncMock(return_value=[])
    return pg


def _redis_store_mock() -> AsyncMock:
    redis = AsyncMock()
    redis.save = AsyncMock()
    redis.load = AsyncMock(return_value=None)
    redis.list_recent = AsyncMock(return_value=[])
    return redis


def _summary() -> SessionSummary:
    return SessionSummary(
        session_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        updated_at=datetime.now(UTC),
    )


async def test_tiered_save_writes_redis_then_pg():
    redis = _redis_store_mock()
    pg = _pg_store_mock()

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    state = SessionState(channel_state={"k": "v"})
    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await tiered.save(org_id, user_id, session_id, state)

    redis.save.assert_awaited_once_with(org_id, user_id, session_id, state)
    pg.save.assert_awaited_once_with(org_id, user_id, session_id, state)


async def test_tiered_save_propagates_pg_failure_even_when_redis_succeeds():
    redis = _redis_store_mock()
    pg = _pg_store_mock()
    pg.save = AsyncMock(side_effect=ConnectionError("PG down"))

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    with pytest.raises(ConnectionError):
        await tiered.save(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), SessionState())

    redis.save.assert_awaited_once()


async def test_tiered_save_continues_when_redis_fails_but_pg_failure_propagates():
    redis = _redis_store_mock()
    redis.save = AsyncMock(side_effect=ConnectionError("Redis down"))
    pg = _pg_store_mock()
    pg.save = AsyncMock(side_effect=RuntimeError("PG also down"))

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    with pytest.raises(RuntimeError):
        await tiered.save(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), SessionState())

    redis.save.assert_awaited_once()
    pg.save.assert_awaited_once()


async def test_tiered_save_swallows_redis_when_pg_succeeds():
    redis = _redis_store_mock()
    redis.save = AsyncMock(side_effect=ConnectionError("Redis down"))
    pg = _pg_store_mock()

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    await tiered.save(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), SessionState())
    pg.save.assert_awaited_once()


async def test_tiered_load_hits_redis_first():
    state = SessionState(channel_state={"k": "redis-value"})
    redis = _redis_store_mock()
    redis.load = AsyncMock(return_value=state)
    pg = _pg_store_mock()

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    loaded = await tiered.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert loaded == state
    pg.load.assert_not_awaited()


async def test_tiered_load_falls_back_to_pg_on_cache_miss_and_warms_cache():
    state = SessionState(channel_state={"k": "pg-value"})
    redis = _redis_store_mock()
    pg = _pg_store_mock()
    pg.load = AsyncMock(return_value=state)

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    loaded = await tiered.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert loaded == state
    pg.load.assert_awaited_once()
    redis.save.assert_awaited_once()  # cache-warm


async def test_tiered_load_falls_back_to_pg_on_redis_failure():
    state = SessionState(channel_state={"k": "pg-value"})
    redis = _redis_store_mock()
    redis.load = AsyncMock(side_effect=ConnectionError("Redis down"))
    pg = _pg_store_mock()
    pg.load = AsyncMock(return_value=state)

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    loaded = await tiered.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert loaded == state
    pg.load.assert_awaited_once()


async def test_tiered_load_returns_none_when_both_backends_miss():
    redis = _redis_store_mock()
    pg = _pg_store_mock()
    pg.load = AsyncMock(return_value=None)

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    loaded = await tiered.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert loaded is None
    redis.save.assert_not_awaited()


async def test_tiered_list_recent_prefers_redis_when_available():
    summaries = [_summary()]
    redis = _redis_store_mock()
    redis.list_recent = AsyncMock(return_value=summaries)
    pg = _pg_store_mock()

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    result = await tiered.list_recent(uuid.uuid4(), uuid.uuid4())

    assert result == summaries
    pg.list_recent.assert_not_awaited()


async def test_tiered_list_recent_falls_back_to_pg_on_redis_failure():
    pg_summaries = [_summary()]
    redis = _redis_store_mock()
    redis.list_recent = AsyncMock(side_effect=ConnectionError("Redis down"))
    pg = _pg_store_mock()
    pg.list_recent = AsyncMock(return_value=pg_summaries)

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    result = await tiered.list_recent(uuid.uuid4(), uuid.uuid4())

    assert result == pg_summaries


async def test_tiered_list_recent_falls_back_to_pg_when_redis_returns_empty():
    """Empty Redis list SHALL also trigger PG fallback (defensive)."""
    pg_summaries = [_summary()]
    redis = _redis_store_mock()
    redis.list_recent = AsyncMock(return_value=[])
    pg = _pg_store_mock()
    pg.list_recent = AsyncMock(return_value=pg_summaries)

    tiered = TieredSessionStateStore(redis_store=redis, postgres_store=pg)
    result = await tiered.list_recent(uuid.uuid4(), uuid.uuid4())

    assert result == pg_summaries
    pg.list_recent.assert_awaited_once()
