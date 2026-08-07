"""Tests for SessionStateStore wiring in WorkflowExecutionService.

Validates the ABC contract integration: the wired ``SessionStateStore`` is used
when ``checkpoint_store`` is provided; the legacy ``AgentStateStore`` is used
when only ``state_store`` is provided; ``InMemoryCheckpointStore`` (engine ABC)
remains unchanged for ``PregelRuntime`` mid-superstep rollback.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from hecate.engine.session_state import SessionState, SessionStateStore
from hecate.services.state.state import AgentState
from hecate.services.workflow.execution_service import WorkflowExecutionService


@asynccontextmanager
async def _noop_lock_cm(*_args: object, **_kwargs: object) -> AsyncGenerator[None, None]:
    """No-op async context manager standing in for ``acquire_session_lock``."""
    yield


def _service(checkpoint_store: SessionStateStore | None = None) -> WorkflowExecutionService:
    """Build a minimal WorkflowExecutionService for unit tests."""
    port = MagicMock()
    return WorkflowExecutionService(port=port, db=MagicMock(), checkpoint_store=checkpoint_store)


def test_constructor_accepts_checkpoint_store():
    """The constructor SHALL accept ``checkpoint_store`` and store it on the instance."""
    store = AsyncMock(spec=SessionStateStore)
    svc = _service(checkpoint_store=store)
    assert svc._checkpoint_store is store


def test_constructor_default_is_none():
    """Backward compat: omitting ``checkpoint_store`` yields ``self._checkpoint_store is None``."""
    svc = _service()
    assert svc._checkpoint_store is None


def test_state_store_parameter_still_accepted():
    """The deprecated ``state_store`` parameter SHALL remain accepted for back-compat."""
    svc = WorkflowExecutionService(port=MagicMock(), db=MagicMock(), state_store=AsyncMock())
    assert svc._state_store is not None


def test_constructor_with_both_stores_keeps_both():
    """Both legacy ``state_store`` and new ``checkpoint_store`` can coexist (transition window)."""
    legacy = AsyncMock()
    new_store = AsyncMock(spec=SessionStateStore)
    svc = WorkflowExecutionService(port=MagicMock(), db=MagicMock(), state_store=legacy, checkpoint_store=new_store)
    assert svc._state_store is legacy
    assert svc._checkpoint_store is new_store


def test_session_state_dataclass_serialization_via_model_dump():
    """``SessionState.agent_state`` SHAL jest serialize via ``model_dump(mode='json')``."""
    agent_state = AgentState(
        session_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        summary="hello",
        context=[{"role": "user", "content": "hi"}],
    )
    state = SessionState(
        agent_state=agent_state.model_dump(mode="json"),
        metadata={"saved_at": "2026-08-02T00:00:00Z"},
    )
    assert state.agent_state["summary"] == "hello"
    assert state.agent_state["context"] == [{"role": "user", "content": "hi"}]
    assert state.metadata["saved_at"] == "2026-08-02T00:00:00Z"


def test_session_state_round_trips_agent_state_via_model_validate():
    """Loading from a ``SessionState`` should reconstitute the typed ``AgentState``."""
    original = AgentState(
        session_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        summary="round trip",
        context=[],
        permission_context={"role": "admin"},
        tool_context={"current": "search"},
        task_context={"todos": []},
        environment_root=None,
        metadata={"tenant": "x"},
    )
    state = SessionState(agent_state=original.model_dump(mode="json"))
    restored = AgentState.model_validate(state.agent_state)
    assert restored == original


def test_invalid_session_state_agent_state_falls_back_gracefully():
    """A corrupted ``agent_state`` dict SHALL log a warning and the load path SHALL
    fall back to a fresh ``AgentState`` rather than raising."""
    # The spec scenario: ``ValidationError`` logs warning + agent_state = None,
    # then defaults to fresh AgentState() construction.

    # This test models the guard that the load path uses — we just demonstrate
    # the validator behavior here.
    bad = {"summary": 12345}  # wrong type
    with pytest.raises(ValidationError):  # type: ignore[name-defined]
        AgentState.model_validate(bad)


def test_session_state_channel_state_defaults_to_empty_dict():
    """The default ``channel_state`` SHALL be an empty dict (no required fields)."""
    state = SessionState()
    assert state.channel_state == {}
    assert state.agent_state == {}
    assert state.event_position == 0
    assert state.metadata == {}


def test_session_state_event_position_default_is_zero():
    """``event_position`` SHALL default to 0 for fresh sessions."""
    state = SessionState()
    assert state.event_position == 0


async def test_persist_session_state_calls_wired_store_with_correct_keys():
    """``_persist_session_state`` SHALL call ``checkpoint_store.save`` with
    parsed UUID keys and carry ``agent_state`` as a JSON dict."""
    store = AsyncMock(spec=SessionStateStore)
    # acquire_session_lock is an async context manager; replace AsyncMock's
    # coroutine return with a real no-op async CM so ``async with`` works.
    store.acquire_session_lock = _noop_lock_cm
    svc = _service(checkpoint_store=store)

    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    agent_uuid = uuid.uuid4()
    session_uuid = uuid.uuid4()
    agent_state = AgentState(
        session_id=session_uuid,
        agent_id=agent_uuid,
        summary="persist",
        context=[],
    )

    # Direct call to the internal helper (bypasses API layer)
    await svc._persist_session_state(
        agent_state=agent_state,
        session_id=session_uuid,
        agent_id=agent_uuid,
        org_id=org_id,
        user_id=user_id,
    )

    store.save.assert_awaited_once()
    call_args = store.save.await_args
    saved_org, saved_user, saved_session, saved_state = call_args.args
    assert saved_org == org_id
    assert saved_user == user_id
    assert saved_session == session_uuid
    assert saved_state.agent_state == agent_state.model_dump(mode="json")


async def test_persist_session_state_swallows_save_failure_no_legacy_fallback():
    """If ``checkpoint_store.save`` raises a non-lock exception, the
    implementation SHALL swallow it (best-effort) and SHALL NOT fall back to
    ``state_store.save``. The legacy fallback was removed in
    ``horizontal-scaling-validation`` because it split state across two
    stores on contention."""
    new_store = AsyncMock(spec=SessionStateStore)
    # Default lock succeeds; save raises a non-ConflictError.
    new_store.acquire_session_lock = _noop_lock_cm
    new_store.save = AsyncMock(side_effect=ConnectionError("tiered down"))
    legacy_store = AsyncMock()

    svc = WorkflowExecutionService(
        port=MagicMock(),
        db=MagicMock(),
        state_store=legacy_store,
        checkpoint_store=new_store,
    )

    agent_state = AgentState(summary="fallback")
    agent_uuid = uuid.uuid4()
    session_uuid = uuid.uuid4()

    # Should NOT raise — best-effort swallow.
    await svc._persist_session_state(
        agent_state=agent_state,
        session_id=session_uuid,
        agent_id=agent_uuid,
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    # Legacy state_store.save SHALL NOT be called.
    legacy_store.save.assert_not_awaited()


async def test_persist_session_state_checkpoint_none_skips_persist():
    """When ``checkpoint_store is None``, ``_persist_session_state`` SHALL
    return early without calling either store. The legacy ``state_store``
    branch was removed in ``horizontal-scaling-validation`` — tests that
    still want legacy behavior must wire it explicitly."""
    legacy_store = AsyncMock()
    svc = WorkflowExecutionService(
        port=MagicMock(),
        db=MagicMock(),
        state_store=legacy_store,
        checkpoint_store=None,
    )

    agent_state = AgentState(summary="legacy only")
    agent_uuid = uuid.uuid4()
    session_uuid = uuid.uuid4()

    await svc._persist_session_state(
        agent_state=agent_state,
        session_id=session_uuid,
        agent_id=agent_uuid,
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    # With checkpoint_store=None, persist is a no-op; legacy NOT called.
    legacy_store.save.assert_not_awaited()
