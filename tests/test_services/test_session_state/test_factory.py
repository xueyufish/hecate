"""Unit tests for ``create_session_state_store`` factory function.

Validates the four supported backends and the unknown-backend error path.
The Redis and Postgres backends are exercised separately; this file only
covers the factory's selection logic.
"""

from __future__ import annotations

import pytest

from hecate.core.config import Settings
from hecate.runtime.session_state import InMemorySessionStateStore
from hecate.studio.session_state import (
    PostgresSessionStateStore,
    RedisSessionStateStore,
    TieredSessionStateStore,
    create_session_state_store,
)


def test_factory_returns_in_memory_for_memory_backend():
    settings = Settings(SESSION_STATE_STORE_BACKEND="memory")
    store = create_session_state_store(settings)
    assert isinstance(store, InMemorySessionStateStore)


def test_factory_returns_redis_for_redis_backend():
    settings = Settings(
        SESSION_STATE_STORE_BACKEND="redis",
        SESSION_STATE_REDIS_URL="redis://localhost:6379/0",
    )
    store = create_session_state_store(settings)
    assert isinstance(store, RedisSessionStateStore)


def test_factory_raises_when_redis_backend_has_no_url():
    settings = Settings(
        SESSION_STATE_STORE_BACKEND="redis",
        SESSION_STATE_REDIS_URL="",
    )
    with pytest.raises(ValueError, match="SESSION_STATE_REDIS_URL"):
        create_session_state_store(settings)


def test_factory_returns_postgres_for_postgres_backend():
    settings = Settings(SESSION_STATE_STORE_BACKEND="postgres")
    store = create_session_state_store(settings)
    assert isinstance(store, PostgresSessionStateStore)


def test_factory_returns_tiered_for_tiered_backend():
    settings = Settings(
        SESSION_STATE_STORE_BACKEND="tiered",
        SESSION_STATE_REDIS_URL="redis://localhost:6379/0",
    )
    store = create_session_state_store(settings)
    assert isinstance(store, TieredSessionStateStore)


def test_factory_raises_when_tiered_backend_has_no_url():
    settings = Settings(
        SESSION_STATE_STORE_BACKEND="tiered",
        SESSION_STATE_REDIS_URL="",
    )
    with pytest.raises(ValueError, match="SESSION_STATE_REDIS_URL"):
        create_session_state_store(settings)


def test_factory_raises_value_error_for_unknown_backend():
    settings = Settings(SESSION_STATE_STORE_BACKEND="elasticsearch")
    with pytest.raises(ValueError, match="Unsupported SESSION_STATE_STORE_BACKEND"):
        create_session_state_store(settings)


def test_factory_default_backend_is_memory():
    """Default settings (no overrides) SHALL select the InMemorySessionStateStore."""
    settings = Settings()
    assert settings.SESSION_STATE_STORE_BACKEND == "memory"
    assert isinstance(create_session_state_store(settings), InMemorySessionStateStore)
