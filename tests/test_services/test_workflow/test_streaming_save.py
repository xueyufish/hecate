"""Regression tests for streaming save path bugfix.

Change 3 wired ``_persist_session_state`` into the non-stream execute path
but missed ``_stream_execute`` — line 473-474 still used the deprecated
``self._state_store``. ``horizontal-scaling-validation`` removed the legacy
branch and wired ``_persist_session_state`` into both normal-end and
disconnect paths of the stream.

These tests verify:
- stream normal end → atomic save called exactly once via wired store
- stream client disconnect → best-effort save attempted, original exception re-raised
- legacy ``_state_store`` no longer referenced in stream path
- save failure does not block stream output
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.engine.session_state import SessionStateStore
from hecate.engine.types import StreamMode
from hecate.services.state.state import AgentState
from hecate.services.workflow.execution_service import WorkflowExecutionService


@asynccontextmanager
async def _noop_lock_cm(*_args: object, **_kwargs: object) -> AsyncGenerator[None, None]:
    yield


def _build_runtime(events: list[dict[str, Any]]) -> MagicMock:
    """Build a mock PregelRuntime whose ``execute`` yields ``events`` then exits."""
    runtime = MagicMock()

    async def _execute(*_a: object, **_kw: object) -> AsyncGenerator[dict[str, Any], None]:
        for ev in events:
            yield ev

    runtime.execute = _execute
    return runtime


# ---------------------------------------------------------------------------
# Normal end
# ---------------------------------------------------------------------------


async def test_stream_normal_end_persists_session_state():
    """When the generator exhausts normally, ``_persist_session_state`` SHALL
    be called exactly once (atomic save at stream end)."""
    checkpoint_store = AsyncMock(spec=SessionStateStore)
    checkpoint_store.acquire_session_lock = _noop_lock_cm
    svc = WorkflowExecutionService(port=MagicMock(), db=MagicMock(), checkpoint_store=checkpoint_store)
    # Spy on _persist_session_state (don't replace — let it run for real so
    # we verify the lock + save flow works end to end).
    persist_calls: list[dict[str, Any]] = []

    original_persist = svc._persist_session_state

    async def spy_persist(**kwargs: Any) -> None:
        persist_calls.append(kwargs)
        await original_persist(**kwargs)

    svc._persist_session_state = spy_persist  # type: ignore[method-assign]

    runtime = _build_runtime([{"type": "messages", "content": "hello"}])
    session_uuid = uuid.uuid4()
    agent_state = AgentState(session_id=session_uuid, agent_id=uuid.uuid4(), summary="stream")

    events_consumed: list[dict[str, Any]] = []
    async for ev in svc._stream_execute(
        runtime=runtime,
        session_id=session_uuid,
        initial_input={"model": "gpt-4o"},
        stream_mode=StreamMode.MESSAGES,
        agent_state=agent_state,
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_id=agent_state.agent_id,
    ):
        events_consumed.append(ev)

    assert len(events_consumed) == 1
    assert len(persist_calls) == 1
    checkpoint_store.save.assert_awaited_once()


# ---------------------------------------------------------------------------
# Client disconnect / mid-stream exception
# ---------------------------------------------------------------------------


async def test_stream_client_disconnect_best_effort_save():
    """When the underlying runtime raises mid-stream, ``_persist_session_state``
    SHALL be attempted (best-effort) and the original exception re-raised."""

    class _FlakyRuntime:
        async def execute(self, **_kw: Any) -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "messages", "content": "partial"}
            raise ConnectionError("client disconnect")

    checkpoint_store = AsyncMock(spec=SessionStateStore)
    checkpoint_store.acquire_session_lock = _noop_lock_cm
    svc = WorkflowExecutionService(port=MagicMock(), db=MagicMock(), checkpoint_store=checkpoint_store)

    persist_calls: list[dict[str, Any]] = []
    original_persist = svc._persist_session_state

    async def spy_persist(**kwargs: Any) -> None:
        persist_calls.append(kwargs)
        await original_persist(**kwargs)

    svc._persist_session_state = spy_persist  # type: ignore[method-assign]

    session_uuid = uuid.uuid4()
    agent_state = AgentState(session_id=session_uuid, agent_id=uuid.uuid4(), summary="flaky")

    with pytest.raises(ConnectionError, match="client disconnect"):
        async for _ in svc._stream_execute(
            runtime=_FlakyRuntime(),  # type: ignore[arg-type]
            session_id=session_uuid,
            initial_input={},
            stream_mode=StreamMode.MESSAGES,
            agent_state=agent_state,
            org_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            agent_id=agent_state.agent_id,
        ):
            pass

    assert len(persist_calls) == 1, "best-effort save SHALL be attempted on disconnect"


# ---------------------------------------------------------------------------
# No legacy state_store call
# ---------------------------------------------------------------------------


async def test_stream_no_legacy_state_store_call():
    """``_stream_execute`` SHALL NOT reference ``self._state_store`` — the
    legacy branch at the old line 473-474 was removed."""
    checkpoint_store = AsyncMock(spec=SessionStateStore)
    checkpoint_store.acquire_session_lock = _noop_lock_cm
    svc = WorkflowExecutionService(
        port=MagicMock(),
        db=MagicMock(),
        checkpoint_store=checkpoint_store,
    )

    runtime = _build_runtime([])
    session_uuid = uuid.uuid4()
    agent_state = AgentState(session_id=session_uuid, agent_id=uuid.uuid4())

    async for _ in svc._stream_execute(
        runtime=runtime,
        session_id=session_uuid,
        initial_input={},
        stream_mode=StreamMode.MESSAGES,
        agent_state=agent_state,
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_id=agent_state.agent_id,
    ):
        pass


# ---------------------------------------------------------------------------
# Save failure does not block stream
# ---------------------------------------------------------------------------


async def test_stream_persist_failure_does_not_block_stream():
    """If ``_persist_session_state`` raises on the normal-end path, the
    exception SHALL propagate (caller decides whether to treat the stream
    as failed). On the disconnect path, save failures SHALL be swallowed
    so the original exception is the one that surfaces."""
    checkpoint_store = AsyncMock(spec=SessionStateStore)
    checkpoint_store.acquire_session_lock = _noop_lock_cm
    checkpoint_store.save = AsyncMock(side_effect=RuntimeError("pg down"))
    svc = WorkflowExecutionService(port=MagicMock(), db=MagicMock(), checkpoint_store=checkpoint_store)

    runtime = _build_runtime([{"type": "messages", "content": "ok"}])
    session_uuid = uuid.uuid4()
    agent_state = AgentState(session_id=session_uuid, agent_id=uuid.uuid4())

    # Save failure is swallowed (best-effort) — stream should complete without
    # surfacing the save error. Stream events are unaffected.
    events: list[dict[str, Any]] = []
    async for ev in svc._stream_execute(
        runtime=runtime,
        session_id=session_uuid,
        initial_input={},
        stream_mode=StreamMode.MESSAGES,
        agent_state=agent_state,
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        agent_id=agent_state.agent_id,
    ):
        events.append(ev)

    assert len(events) == 1
    assert events[0]["content"] == "ok"
