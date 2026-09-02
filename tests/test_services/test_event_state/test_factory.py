"""Unit tests for ``create_event_store`` factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hecate.runtime.eventstore import InMemoryEventStore
from hecate.services.event_state.factory import SUPPORTED_BACKENDS, create_event_store
from hecate.services.event_state.postgres_store import PostgresEventStore


def _settings(**overrides):
    s = MagicMock()
    s.EVENT_STORE_BACKEND = "memory"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_factory_memory_default_returns_inmemory():
    """``EVENT_STORE_BACKEND="memory"`` (default) SHALL return InMemoryEventStore."""
    settings = _settings(EVENT_STORE_BACKEND="memory")
    store = create_event_store(settings)
    assert isinstance(store, InMemoryEventStore)


def test_factory_postgres_returns_postgres_event_store():
    """``EVENT_STORE_BACKEND="postgres"`` SHALL return PostgresEventStore."""
    settings = _settings(EVENT_STORE_BACKEND="postgres")
    fake_factory = MagicMock()
    with patch("hecate.core.database.async_session_factory", fake_factory):
        store = create_event_store(settings)
    assert isinstance(store, PostgresEventStore)
    assert store._tenant_context_provider is not None


def test_factory_unknown_backend_raises_valueerror():
    """Unknown backend SHALL raise ValueError listing supported values."""
    settings = _settings(EVENT_STORE_BACKEND="kafka")
    with pytest.raises(ValueError) as exc_info:
        create_event_store(settings)
    msg = str(exc_info.value)
    assert "kafka" in msg
    for backend in SUPPORTED_BACKENDS:
        assert backend in msg
