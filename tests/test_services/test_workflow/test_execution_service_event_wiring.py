"""Tests for EventStore wiring in WorkflowExecutionService.

Validates:
- ``event_store`` parameter accepted by ``__init__`` and stored as ``self._event_store``
- Default ``None`` preserves pre-Change-5 behavior
- ``_sync_event_position`` synchronizes ``SessionState.event_position`` with
  ``event_store.get_version(session_id)``
- ``_sync_event_position`` is a no-op when ``event_store is None``
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from hecate.runtime.eventstore import EventStore, InMemoryEventStore
from hecate.runtime.session_state import SessionState
from hecate.services.workflow.execution_service import WorkflowExecutionService, _sync_event_position


def _service(event_store: EventStore | None = None) -> WorkflowExecutionService:
    port = MagicMock()
    return WorkflowExecutionService(port=port, db=MagicMock(), event_store=event_store)


def test_constructor_accepts_event_store():
    """The constructor SHALL accept ``event_store`` and store it on the instance."""
    store = InMemoryEventStore()
    svc = _service(event_store=store)
    assert svc._event_store is store


def test_constructor_default_event_store_is_none():
    """Backward compat: omitting ``event_store`` yields ``self._event_store is None``."""
    svc = _service()
    assert svc._event_store is None


async def test_sync_event_position_returns_state_unchanged_when_no_store():
    """``_sync_event_position`` SHALL be a no-op when ``event_store is None``."""
    state = SessionState(agent_state={"k": "v"})
    session_id = uuid.uuid4()
    result = await _sync_event_position(state, None, session_id)
    assert result is state
    assert result.event_position == 0


async def test_sync_event_position_updates_position_when_store_has_events():
    """``_sync_event_position`` SHALL set ``event_position`` to ``get_version(session_id)``."""
    from hecate.runtime.eventstore import Event, EventType

    session_id = uuid.uuid4()
    store = InMemoryEventStore()
    for _ in range(7):
        await store.append(Event(session_id=session_id, superstep=0, event_type=EventType.NODE_START))

    state = SessionState(agent_state={"k": "v"})
    result = await _sync_event_position(state, store, session_id)
    assert result.event_position == 7
    assert result is not state  # model_copy returns a new instance


async def test_sync_event_position_zero_for_empty_store():
    """``_sync_event_position`` SHALL set ``event_position`` to 0 when store is empty."""
    session_id = uuid.uuid4()
    store = InMemoryEventStore()
    state = SessionState(agent_state={"k": "v"})
    result = await _sync_event_position(state, store, session_id)
    assert result.event_position == 0


async def test_sync_event_position_uses_provided_session_id():
    """``_sync_event_position`` SHALL query the store with the provided session_id, not the state's."""
    from hecate.runtime.eventstore import Event, EventType

    target_session = uuid.uuid4()
    other_session = uuid.uuid4()
    store = InMemoryEventStore()
    await store.append(Event(session_id=target_session, superstep=0, event_type=EventType.NODE_END))
    for _ in range(5):
        await store.append(Event(session_id=other_session, superstep=0, event_type=EventType.NODE_START))

    state = SessionState(agent_state={})
    result = await _sync_event_position(state, store, target_session)
    assert result.event_position == 1
