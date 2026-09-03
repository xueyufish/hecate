"""Unit tests for ``RedisSessionStateStore`` backed by fakeredis.

Validates the ABC contract for the Redis-only implementation: save+load
round-trip, TTL semantics, hash-tag key layout, multi-tenant isolation,
list_recent ordering, and graceful failure handling when Redis raises.
"""

from __future__ import annotations

import uuid

from hecate.runtime.session_state import SessionState
from hecate.studio.session_state.redis_store import RedisSessionStateStore


async def test_redis_store_save_then_load_round_trips(monkeypatch, fakeredis_client):
    """Save and then load SHALL preserve all user-provided fields. The store
    stamps ``_saved_at`` into metadata for ``list_recent`` ordering, but
    user-supplied metadata fields SHALL be preserved intact."""
    store = RedisSessionStateStore(redis_url="redis://test", key_prefix="test:", ttl_seconds=60)
    monkeypatch.setattr(store, "_get_redis", lambda: _await(fakeredis_client))

    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    state = SessionState(channel_state={"k": "v"}, agent_state={"a": 1}, event_position=3, metadata={"superstep": 7})

    await store.save(org_id, user_id, session_id, state)
    loaded = await store.load(org_id, user_id, session_id)

    assert loaded is not None
    assert loaded.channel_state == {"k": "v"}
    assert loaded.agent_state == {"a": 1}
    assert loaded.event_position == 3
    assert loaded.metadata is not None
    assert loaded.metadata["superstep"] == 7
    assert "_saved_at" in loaded.metadata


async def test_redis_store_save_uses_key_with_org_id_hash_tag(monkeypatch, fakeredis_client):
    """The constructed key SHALL contain ``{org_id}`` so Redis Cluster routes
    same-tenant keys to the same slot."""
    store = RedisSessionStateStore(redis_url="redis://test", key_prefix="h:")
    monkeypatch.setattr(store, "_get_redis", lambda: _await(fakeredis_client))

    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await store.save(org_id, user_id, session_id, SessionState())

    expected_key = f"h:{org_id}:{user_id}:{session_id}"
    assert await fakeredis_client.exists(expected_key) == 1


async def test_redis_store_load_returns_none_for_unknown_session(monkeypatch, fakeredis_client):
    store = RedisSessionStateStore(redis_url="redis://test")
    monkeypatch.setattr(store, "_get_redis", lambda: _await(fakeredis_client))

    loaded = await store.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert loaded is None


async def test_redis_store_save_swallows_redis_failure(monkeypatch):
    """A failing Redis on ``save`` SHALL be logged and swallowed (best-effort
    cache) — the caller does not see an exception."""
    store = RedisSessionStateStore(redis_url="redis://test")

    class ExplodingRedis:
        async def set(self, *args, **kwargs):
            raise ConnectionError("simulated Redis down")

    async def _explode():
        return ExplodingRedis()

    monkeypatch.setattr(store, "_get_redis", _explode)

    await store.save(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), SessionState())


async def test_redis_store_list_recent_returns_only_same_user(monkeypatch, fakeredis_client):
    """``list_recent`` SHALL filter to the requested ``user_id`` even if the
    SCAN pattern matches more org-wide keys."""
    store = RedisSessionStateStore(redis_url="redis://test", key_prefix="h:")
    monkeypatch.setattr(store, "_get_redis", lambda: _await(fakeredis_client))

    org_id = uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    sa, sb = uuid.uuid4(), uuid.uuid4()

    await store.save(org_id, user_a, sa, SessionState(metadata={"superstep": 1}))
    await store.save(org_id, user_b, sb, SessionState(metadata={"superstep": 2}))

    a_summaries = await store.list_recent(org_id, user_a, limit=10)
    b_summaries = await store.list_recent(org_id, user_b, limit=10)

    assert {s.session_id for s in a_summaries} == {sa}
    assert {s.session_id for s in b_summaries} == {sb}


async def test_redis_store_list_recent_honors_limit(monkeypatch, fakeredis_client):
    store = RedisSessionStateStore(redis_url="redis://test")
    monkeypatch.setattr(store, "_get_redis", lambda: _await(fakeredis_client))

    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    for _ in range(8):
        await store.save(org_id, user_id, uuid.uuid4(), SessionState())

    summaries = await store.list_recent(org_id, user_id, limit=3)
    assert len(summaries) == 3


async def test_redis_store_set_ex_ttl_applied(monkeypatch, fakeredis_client):
    """save SHALL pass ``ex=ttl_seconds`` so Redis expires the key automatically."""
    store = RedisSessionStateStore(redis_url="redis://test", ttl_seconds=42)
    monkeypatch.setattr(store, "_get_redis", lambda: _await(fakeredis_client))

    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await store.save(org_id, user_id, session_id, SessionState())

    key = f"hecate:state:{org_id}:{user_id}:{session_id}"
    ttl = await fakeredis_client.ttl(key)
    assert 0 < ttl <= 42


class _Awaitable:
    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()


def _await(value):
    """Helper that lets ``monkeypatch.setattr`` return an awaitable."""
    return _AwaitableWithValue(value)


class _AwaitableWithValue(_Awaitable):
    def __init__(self, value):
        self._value = value
