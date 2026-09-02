"""Unit tests for ``get_event_store`` FastAPI dependency."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from starlette.requests import Request

from hecate.core.deps_event_store import get_event_store
from hecate.runtime.eventstore import InMemoryEventStore


def _make_request(app: FastAPI) -> Request:
    scope = {"type": "http", "app": app}
    return Request(scope)


def test_get_event_store_reads_app_state_singleton():
    """When app.state.event_store is set, the dependency SHALL return it."""
    app = FastAPI()
    sentinel = InMemoryEventStore()
    app.state.event_store = sentinel

    request = _make_request(app)
    store = get_event_store(request)
    assert store is sentinel


def test_get_event_store_falls_back_to_factory_when_unset():
    """When app.state.event_store is unset, the dependency SHALL call create_event_store(settings)."""
    app = FastAPI()

    fallback_store = InMemoryEventStore()
    with patch("hecate.core.deps_event_store.create_event_store", return_value=fallback_store) as factory:
        request = _make_request(app)
        store = get_event_store(request)
    assert store is fallback_store
    factory.assert_called_once()


def test_get_event_store_returns_eventstore_instance():
    """The dependency SHALL always return an EventStore instance."""
    app = FastAPI()
    app.state.event_store = InMemoryEventStore()
    request = _make_request(app)
    store = get_event_store(request)
    assert isinstance(store, InMemoryEventStore)
