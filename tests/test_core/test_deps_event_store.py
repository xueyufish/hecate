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


def test_get_event_store_falls_back_to_shared_instance_when_unset():
    """When app.state.event_store is unset, the dependency SHALL resolve the
    process-wide shared store from entry_assembly — never a fresh per-call
    store (in-memory backends make store identity the consistency boundary)."""
    app = FastAPI()

    shared = InMemoryEventStore()
    with patch("hecate.core.composition.entry_assembly._shared_event_store", shared):
        request = _make_request(app)
        store = get_event_store(request)
    assert store is shared


def test_get_event_store_returns_eventstore_instance():
    """The dependency SHALL always return an EventStore instance."""
    app = FastAPI()
    app.state.event_store = InMemoryEventStore()
    request = _make_request(app)
    store = get_event_store(request)
    assert isinstance(store, InMemoryEventStore)
