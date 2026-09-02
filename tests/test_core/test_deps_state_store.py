"""Tests for the FastAPI ``get_session_state_store`` dependency.

Verifies the lookup-vs-fallback contract: in a real FastAPI request, the
dependency returns ``app.state.session_state_store``; outside the lifespan
(where ``app.state`` lacks the attribute), the dependency falls back to
``create_session_state_store(settings)``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hecate.core.config import settings
from hecate.core.deps_state_store import get_session_state_store
from hecate.runtime.session_state import InMemorySessionStateStore, SessionStateStore
from hecate.services.session_state import create_session_state_store


class _FakeRequest:
    """Minimal stand-in for FastAPI ``Request`` exposing ``app.state``."""

    def __init__(self, state: object) -> None:
        self._state = state

    @property
    def app(self) -> object:
        return self._state


class _FakeAppWithState:
    def __init__(self, state_value: object) -> None:
        self.state = MagicMock(spec=["session_state_store"])
        self.state.session_state_store = state_value


class _FakeAppEmptyState:
    def __init__(self) -> None:
        self.state = MagicMock(spec=[])


def test_dependency_returns_app_state_singleton_when_set():
    """When ``app.state.session_state_store`` exists, the dependency SHALL return it."""
    sentinel = MagicMock(spec=SessionStateStore)
    request = _FakeRequest(_FakeAppWithState(sentinel))
    result = get_session_state_store(request)
    assert result is sentinel


def test_dependency_falls_back_to_factory_when_state_unset():
    """When ``app.state.session_state_store`` is unset, the dependency SHALL
    fall back to ``create_session_state_store(settings)``."""
    request = _FakeRequest(_FakeAppEmptyState())
    result = get_session_state_store(request)
    assert isinstance(result, SessionStateStore)


def test_factory_default_returns_in_memory_for_default_backend():
    """The factory default backend is ``memory`` so the fallback SHALL return
    :class:`InMemorySessionStateStore`."""
    assert settings.SESSION_STATE_STORE_BACKEND == "memory"
    factory_result = create_session_state_store(settings)
    assert isinstance(factory_result, InMemorySessionStateStore)


def test_fallback_save_then_load_round_trips():
    """The fallback path SHALL produce a usable store that round-trips save/load."""
    from hecate.runtime.session_state import SessionState

    request = _FakeRequest(_FakeAppEmptyState())
    store = get_session_state_store(request)

    org_id = uuid = pytest.importorskip("uuid").uuid4()
    user_id = uuid
    session_id = uuid
    state = SessionState(metadata={"round": "trip"})
    # Re-import-bound for clarity
    from uuid import uuid4

    org_id = uuid4()
    user_id = uuid4()
    session_id = uuid4()
    state = SessionState(metadata={"round": "trip"})

    # Use asyncio.run because the dependency itself is sync
    import asyncio

    asyncio.run(store.save(org_id, user_id, session_id, state))
    loaded = asyncio.run(store.load(org_id, user_id, session_id))
    assert loaded == state
