"""Tests for SessionStateMaterializer adapter."""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.eventstore import EventStore, InMemoryEventStore
from hecate.runtime.session_state import InMemorySessionStateStore
from hecate.runtime.session_state_materializer import (
    SessionStateMaterializer,
    _bounded_retain,
    _project_channel_state,
)


@pytest.fixture
def store() -> InMemorySessionStateStore:
    return InMemorySessionStateStore()


@pytest.fixture
def event_store() -> EventStore:
    return InMemoryEventStore()


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def session_id() -> uuid.UUID:
    return uuid.uuid4()


def test_bounded_retain_keeps_small_strings_intact():
    assert _bounded_retain("hello") == {"value": "hello"}


def test_bounded_retain_truncates_large_strings():
    big = "x" * 50_000
    out = _bounded_retain(big, head_chars=10, tail_chars=5)
    assert out["_omitted"] is True
    assert len(out["_prefix"]) == 10
    assert len(out["_suffix"]) == 5
    assert out["_omitted_bytes"] == 50_000 - 15


def test_project_channel_state_skips_underscore_and_sys_channels():
    state = {
        "messages": {"role": "user", "content": "hi"},
        "_session_id": "should-be-omitted",
        "sys.execution_mode": "should-be-omitted",
        "_tools": ["should-be-omitted"],
    }
    projected = _project_channel_state(state)
    assert "messages" in projected
    assert "_session_id" not in projected
    assert "_tools" not in projected
    assert "sys.execution_mode" not in projected


@pytest.mark.asyncio
async def test_save_persists_channel_state_under_tenant_triple(
    store: InMemorySessionStateStore,
    event_store: EventStore,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> None:
    captured: tuple[uuid.UUID, uuid.UUID] | None = None

    def provider() -> tuple[uuid.UUID, uuid.UUID] | None:
        nonlocal captured
        captured = (org_id, user_id)
        return captured

    materializer = SessionStateMaterializer(
        session_state_store=store,
        tenant_context_provider=provider,
        event_store=event_store,
    )

    await materializer.save(
        session_id=session_id,
        superstep=3,
        node_id="agent_a",
        channel_state={"messages": {"role": "user", "content": "hello"}, "_session_id": "skip"},
        metadata={"interrupted": False},
    )

    assert captured == (org_id, user_id)
    persisted = await store.load(org_id, user_id, session_id)
    assert persisted is not None
    assert "messages" in persisted.channel_state
    assert "_session_id" not in persisted.channel_state
    assert persisted.metadata.get("superstep") == 3
    assert persisted.event_position == 0


@pytest.mark.asyncio
async def test_save_skipped_when_no_tenant(
    store: InMemorySessionStateStore,
    session_id: uuid.UUID,
) -> None:
    def provider() -> tuple[uuid.UUID, uuid.UUID] | None:
        return None

    materializer = SessionStateMaterializer(
        session_state_store=store,
        tenant_context_provider=provider,
    )

    returned = await materializer.save(
        session_id=session_id,
        superstep=1,
        node_id="x",
        channel_state={"messages": {"role": "user"}},
    )
    assert isinstance(returned, uuid.UUID)


@pytest.mark.asyncio
async def test_load_returns_dict_with_log_version_when_state_exists(
    store: InMemorySessionStateStore,
    event_store: EventStore,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> None:
    materializer = SessionStateMaterializer(
        session_state_store=store,
        tenant_context_provider=lambda: (org_id, user_id),
        event_store=event_store,
    )
    await materializer.save(
        session_id=session_id,
        superstep=2,
        node_id="x",
        channel_state={"messages": {"role": "user"}},
    )
    record = await materializer.load(session_id)
    assert record is not None
    assert record["superstep"] == 2
    assert record["node_id"] == "x"
    assert "log_version" in record["metadata"]


@pytest.mark.asyncio
async def test_load_returns_none_when_no_state(
    store: InMemorySessionStateStore,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
) -> None:
    materializer = SessionStateMaterializer(
        session_state_store=store,
        tenant_context_provider=lambda: (org_id, user_id),
    )
    assert await materializer.load(session_id) is None
