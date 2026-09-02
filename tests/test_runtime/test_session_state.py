"""Tests for ``SessionState`` data model and ``SessionStateStore`` ABC.

Covers the engine-layer abstractions introduced by the
``session-state-store-abstraction`` change. The Redis/PostgreSQL
implementations are exercised in the follow-up
``session-state-store-redis-pg`` change; this file validates the
contract shared by all backends.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hecate.runtime.session_state import (
    InMemorySessionStateStore,
    SessionNotFoundError,
    SessionState,
    SessionStateStore,
    SessionSummary,
)

# ----------------------------------------------------------------------
# 5.2 SessionState is frozen
# ----------------------------------------------------------------------


def test_session_state_rejects_field_assignment():
    """Mutating a field on an existing SessionState SHALL raise ValidationError."""
    state = SessionState()
    with pytest.raises(ValidationError):
        state.channel_state = {"foo": "bar"}  # type: ignore[misc]


# Note: Pydantic v2 frozen models prevent field REASSIGNMENT but do NOT
# deep-freeze mutable containers (dict/list). Mutating ``state.channel_state['x']``
# is allowed. Implementations MUST treat returned ``SessionState`` as if its
# nested dicts were immutable — call ``model_copy(update=...)`` to change anything.


# ----------------------------------------------------------------------
# 5.3 model_copy produces a new instance
# ----------------------------------------------------------------------


def test_model_copy_returns_new_instance_with_one_field_updated():
    """``model_copy(update=...)`` SHALL return a NEW instance with one field
    changed and all other fields copied unchanged."""
    original = SessionState(
        channel_state={"a": 1},
        agent_state={"b": 2},
        event_position=3,
        metadata={"k": "v"},
    )
    updated = original.model_copy(update={"event_position": 99})

    assert updated is not original
    assert updated.event_position == 99
    assert updated.channel_state == {"a": 1}
    assert updated.agent_state == {"b": 2}
    assert updated.metadata == {"k": "v"}


def test_model_copy_supports_deep_copy_for_isolation():
    """``model_copy(update=..., deep=True)`` SHALL deep-copy nested dicts so
    the returned copy does not share mutable containers with the original.
    Implementations needing isolation MUST pass ``deep=True`` (default in
    many call sites that serialize via ``model_dump_json`` then re-validate)."""
    original = SessionState(channel_state={"shared": True})
    updated = original.model_copy(update={"event_position": 1}, deep=True)

    assert original.channel_state == {"shared": True}
    assert updated.channel_state == {"shared": True}
    # ``deep=True`` produces a fresh dict; the two references are distinct objects.
    assert original.channel_state is not updated.channel_state


# ----------------------------------------------------------------------
# 5.4 JSON round-trip
# ----------------------------------------------------------------------


def test_session_state_json_round_trip_preserves_all_fields():
    """Serialize via model_dump_json then validate via model_validate_json
    SHALL yield a state equal to the original."""
    original = SessionState(
        channel_state={"messages": [{"role": "user", "content": "hi"}]},
        agent_state={"context": [{"type": "human", "text": "hello"}]},
        event_position=42,
        metadata={"superstep": 7, "started_at": "2026-07-31T10:00:00Z"},
    )
    json_str = original.model_dump_json()
    restored = SessionState.model_validate_json(json_str)
    assert restored == original
    assert restored.channel_state == original.channel_state
    assert restored.agent_state == original.agent_state
    assert restored.event_position == original.event_position
    assert restored.metadata == original.metadata


# ----------------------------------------------------------------------
# 5.5 Reject negative event_position
# ----------------------------------------------------------------------


def test_session_state_rejects_negative_event_position():
    """Constructing SessionState with event_position=-1 SHALL raise ValidationError."""
    with pytest.raises(ValidationError):
        SessionState(event_position=-1)


def test_session_state_accepts_zero_event_position():
    """event_position=0 (the default) SHALL be accepted."""
    state = SessionState()
    assert state.event_position == 0


# ----------------------------------------------------------------------
# 5.6 Default values
# ----------------------------------------------------------------------


def test_session_state_default_values():
    """Calling SessionState() with no arguments SHALL yield all-default fields."""
    state = SessionState()
    assert state.channel_state == {}
    assert state.agent_state == {}
    assert state.event_position == 0
    assert state.metadata == {}


# ----------------------------------------------------------------------
# 5.7 ABC cannot be instantiated directly
# ----------------------------------------------------------------------


def test_session_state_store_abc_cannot_be_instantiated():
    """``SessionStateStore()`` SHALL raise TypeError because it has unimplemented
    abstract methods."""
    with pytest.raises(TypeError) as exc_info:
        SessionStateStore()  # type: ignore[abstract]
    assert "abstract" in str(exc_info.value).lower()


# ----------------------------------------------------------------------
# 5.8 InMemory save + load returns the same state
# ----------------------------------------------------------------------


async def test_in_memory_save_then_load_returns_equal_state():
    """A state saved via ``save`` SHALL round-trip via ``load`` with equal fields."""
    store = InMemorySessionStateStore()
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    state = SessionState(
        channel_state={"step": 3},
        agent_state={"summary": "hello"},
        event_position=10,
        metadata={"superstep": 3},
    )

    await store.save(org_id, user_id, session_id, state)
    loaded = await store.load(org_id, user_id, session_id)

    assert loaded is not None
    assert loaded == state


async def test_in_memory_save_overwrites_existing_state():
    """Saving twice for the same key SHALL keep the most recent state."""
    store = InMemorySessionStateStore()
    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await store.save(org_id, user_id, session_id, SessionState(event_position=1))
    await store.save(org_id, user_id, session_id, SessionState(event_position=2))

    loaded = await store.load(org_id, user_id, session_id)
    assert loaded is not None
    assert loaded.event_position == 2


# ----------------------------------------------------------------------
# 5.9 load returns None for unknown session
# ----------------------------------------------------------------------


async def test_in_memory_load_returns_none_for_unknown_org_id():
    store = InMemorySessionStateStore()
    loaded = await store.load(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert loaded is None


async def test_in_memory_load_returns_none_for_known_org_unknown_session():
    store = InMemorySessionStateStore()
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    await store.save(org_id, user_id, uuid.uuid4(), SessionState())
    loaded = await store.load(org_id, user_id, uuid.uuid4())
    assert loaded is None


async def test_in_memory_load_returns_none_for_known_org_user_unknown_session():
    store = InMemorySessionStateStore()
    org_id = uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    await store.save(org_id, user_a, uuid.uuid4(), SessionState())
    loaded = await store.load(org_id, user_b, uuid.uuid4())
    assert loaded is None


# ----------------------------------------------------------------------
# 5.10 list_recent ordering by updated_at descending
# ----------------------------------------------------------------------


async def test_in_memory_list_recent_orders_by_updated_at_descending():
    """Most-recently-saved sessions SHALL appear first."""
    store = InMemorySessionStateStore()
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await store.save(org_id, user_id, s1, SessionState())
    await asyncio.sleep(0.005)
    await store.save(org_id, user_id, s2, SessionState())
    await asyncio.sleep(0.005)
    await store.save(org_id, user_id, s3, SessionState())

    summaries = await store.list_recent(org_id, user_id)
    assert [s.session_id for s in summaries] == [s3, s2, s1]


# ----------------------------------------------------------------------
# 5.11 list_recent org_id isolation
# ----------------------------------------------------------------------


async def test_list_recent_filters_by_org_id():
    """Sessions in different orgs SHALL NOT appear in each other's list_recent."""
    store = InMemorySessionStateStore()
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    user_id = uuid.uuid4()
    sa, sb = uuid.uuid4(), uuid.uuid4()

    await store.save(org_a, user_id, sa, SessionState())
    await store.save(org_b, user_id, sb, SessionState())

    a_summaries = await store.list_recent(org_a, user_id)
    b_summaries = await store.list_recent(org_b, user_id)

    assert {s.session_id for s in a_summaries} == {sa}
    assert {s.session_id for s in b_summaries} == {sb}


# ----------------------------------------------------------------------
# 5.12 list_recent user_id isolation
# ----------------------------------------------------------------------


async def test_list_recent_filters_by_user_id():
    """Sessions for different users within the same org SHALL NOT appear
    in each other's list_recent."""
    store = InMemorySessionStateStore()
    org_id = uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    sa, sb = uuid.uuid4(), uuid.uuid4()

    await store.save(org_id, user_a, sa, SessionState())
    await store.save(org_id, user_b, sb, SessionState())

    a_summaries = await store.list_recent(org_id, user_a)
    b_summaries = await store.list_recent(org_id, user_b)

    assert {s.session_id for s in a_summaries} == {sa}
    assert {s.session_id for s in b_summaries} == {sb}


# ----------------------------------------------------------------------
# 5.13 list_recent limit honored
# ----------------------------------------------------------------------


async def test_list_recent_honors_limit_parameter():
    """``limit`` SHALL cap the number of returned summaries."""
    store = InMemorySessionStateStore()
    org_id, user_id = uuid.uuid4(), uuid.uuid4()

    for _ in range(8):
        await store.save(org_id, user_id, uuid.uuid4(), SessionState())
        await asyncio.sleep(0.002)

    summaries = await store.list_recent(org_id, user_id, limit=5)
    assert len(summaries) == 5


async def test_list_recent_default_limit_is_ten():
    """Default ``limit`` SHALL be 10."""
    store = InMemorySessionStateStore()
    org_id, user_id = uuid.uuid4(), uuid.uuid4()

    for _ in range(15):
        await store.save(org_id, user_id, uuid.uuid4(), SessionState())
        await asyncio.sleep(0.001)

    summaries = await store.list_recent(org_id, user_id)
    assert len(summaries) == 10


# ----------------------------------------------------------------------
# 5.14 SessionNotFoundError carries the (org, user, session) triple
# ----------------------------------------------------------------------


def test_session_not_found_error_message_includes_triple():
    """The exception message SHALL include org_id, user_id, session_id for diagnostics."""
    org_id, user_id, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    err = SessionNotFoundError(org_id, user_id, session_id)
    msg = str(err)
    assert str(org_id) in msg
    assert str(user_id) in msg
    assert str(session_id) in msg


def test_session_not_found_error_is_value_error():
    """SessionNotFoundError SHALL inherit from ValueError so callers can
    catch it via the standard builtin."""
    err = SessionNotFoundError(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert isinstance(err, ValueError)


# ----------------------------------------------------------------------
# 5.15 All ABC methods are async coroutine functions
# ----------------------------------------------------------------------


def test_abc_methods_are_coroutine_functions():
    """All three ABC methods SHALL be ``async def`` so the type system
    enforces async semantics."""
    assert asyncio.iscoroutinefunction(SessionStateStore.save)
    assert asyncio.iscoroutinefunction(SessionStateStore.load)
    assert asyncio.iscoroutinefunction(SessionStateStore.list_recent)


# ----------------------------------------------------------------------
# Cross-cutting: SessionSummary is frozen too
# ----------------------------------------------------------------------


def test_session_summary_is_frozen():
    """SessionSummary SHALL be frozen for the same reason as SessionState."""
    summary = SessionSummary(
        session_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        updated_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        summary.org_id = uuid.uuid4()  # type: ignore[misc]
