"""Tests for Agent State — AgentState model and InMemoryStateStore."""

from __future__ import annotations

import asyncio
import uuid

from hecate.services.state.state import AgentState
from hecate.services.state.store import InMemoryStateStore

# ---------------------------------------------------------------------------
# AgentState model tests (4.1)
# ---------------------------------------------------------------------------


async def test_agent_state_creation_defaults() -> None:
    """AgentState with minimal args has sensible defaults."""
    session_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    state = AgentState(session_id=session_id, agent_id=agent_id)

    assert state.session_id == session_id
    assert state.agent_id == agent_id
    assert state.summary == ""
    assert state.context == []
    assert state.permission_context == {}
    assert state.tool_context == {}
    assert state.task_context == {}
    assert state.environment_root is None
    assert state.metadata == {}


async def test_agent_state_creation_explicit() -> None:
    """AgentState with explicit values stores them correctly."""
    session_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    state = AgentState(
        session_id=session_id,
        agent_id=agent_id,
        summary="test summary",
        context=[{"role": "user", "content": "hello"}],
        permission_context={"allow": ["tool_a"]},
        tool_context={"active": ["group_1"]},
        task_context={"todos": ["task_1"]},
        environment_root="/workspace/env",
        metadata={"key": "value"},
    )

    assert state.summary == "test summary"
    assert state.context == [{"role": "user", "content": "hello"}]
    assert state.permission_context == {"allow": ["tool_a"]}
    assert state.environment_root == "/workspace/env"


async def test_agent_state_model_dump_roundtrip() -> None:
    """model_dump produces a JSON-serializable dict; model_validate restores it."""
    state = AgentState(
        session_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        summary="roundtrip test",
        context=[{"role": "assistant", "content": "hi"}],
    )

    data = state.model_dump()
    assert isinstance(data, dict)
    assert data["summary"] == "roundtrip test"

    restored = AgentState.model_validate(data)
    assert restored.session_id == state.session_id
    assert restored.summary == state.summary
    assert restored.context == state.context


async def test_agent_state_model_validate_from_dict() -> None:
    """model_validate creates AgentState from a plain dict."""
    sid = uuid.uuid4()
    aid = uuid.uuid4()
    data = {
        "session_id": str(sid),
        "agent_id": str(aid),
        "summary": "from dict",
        "context": [],
        "permission_context": {},
        "tool_context": {},
        "task_context": {},
        "environment_root": None,
        "metadata": {},
    }

    state = AgentState.model_validate(data)
    assert state.session_id == sid
    assert state.agent_id == aid
    assert state.summary == "from dict"


# ---------------------------------------------------------------------------
# InMemoryStateStore tests (4.2)
# ---------------------------------------------------------------------------


async def test_store_save_and_load() -> None:
    """Save then load returns the same state."""
    store = InMemoryStateStore()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    state = AgentState(session_id=session_id, agent_id=agent_id, summary="saved")

    await store.save(agent_id, session_id, state)
    loaded = await store.load(agent_id, session_id)

    assert loaded is not None
    assert loaded.summary == "saved"
    assert loaded.session_id == session_id


async def test_store_load_nonexistent() -> None:
    """Load for unknown key returns None."""
    store = InMemoryStateStore()
    loaded = await store.load(uuid.uuid4(), uuid.uuid4())
    assert loaded is None


async def test_store_delete() -> None:
    """Delete removes the state; subsequent load returns None."""
    store = InMemoryStateStore()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    state = AgentState(session_id=session_id, agent_id=agent_id)

    await store.save(agent_id, session_id, state)
    await store.delete(agent_id, session_id)

    loaded = await store.load(agent_id, session_id)
    assert loaded is None


async def test_store_list_sessions() -> None:
    """list_sessions returns summaries for all saved sessions of an agent."""
    store = InMemoryStateStore()
    agent_id = uuid.uuid4()
    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()

    await store.save(agent_id, sid1, AgentState(session_id=sid1, agent_id=agent_id))
    await store.save(agent_id, sid2, AgentState(session_id=sid2, agent_id=agent_id))

    sessions = await store.list_sessions(agent_id)
    session_ids = {s.session_id for s in sessions}
    assert sid1 in session_ids
    assert sid2 in session_ids


async def test_store_different_sessions_independent() -> None:
    """Different sessions of the same agent have independent state."""
    store = InMemoryStateStore()
    agent_id = uuid.uuid4()
    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()

    await store.save(agent_id, sid1, AgentState(session_id=sid1, agent_id=agent_id, summary="session-1"))
    await store.save(agent_id, sid2, AgentState(session_id=sid2, agent_id=agent_id, summary="session-2"))

    loaded1 = await store.load(agent_id, sid1)
    loaded2 = await store.load(agent_id, sid2)

    assert loaded1 is not None and loaded1.summary == "session-1"
    assert loaded2 is not None and loaded2.summary == "session-2"


# ---------------------------------------------------------------------------
# InMemoryStateStore concurrent safety (4.3)
# ---------------------------------------------------------------------------


async def test_store_concurrent_save_no_corruption() -> None:
    """Two coroutines saving the same key concurrently does not corrupt data."""
    store = InMemoryStateStore()
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def save_with_summary(summary: str) -> None:
        state = AgentState(session_id=session_id, agent_id=agent_id, summary=summary)
        await store.save(agent_id, session_id, state)

    # Run both concurrently
    await asyncio.gather(
        save_with_summary("coroutine-a"),
        save_with_summary("coroutine-b"),
    )

    loaded = await store.load(agent_id, session_id)
    assert loaded is not None
    # One of the two summaries should be the final value (no corruption)
    assert loaded.summary in ("coroutine-a", "coroutine-b")
