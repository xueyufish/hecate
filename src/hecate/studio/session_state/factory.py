"""Factory for selecting a SessionStateStore backend at application startup.

Provides :func:`create_session_state_store` that selects an implementation
based on ``settings.SESSION_STATE_STORE_BACKEND``. Supported values:

- ``"memory"`` — :class:`hecate.runtime.session_state.InMemorySessionStateStore`
  (single-process, default for backward compatibility).
- ``"redis"`` — :class:`RedisSessionStateStore` (Redis-only, hot-path cache).
- ``"postgres"`` — :class:`PostgresSessionStateStore` (PG-only, durable truth).
- ``"tiered"`` — :class:`TieredSessionStateStore` (Redis cache + PG truth,
  write-through, recommended for production).

The factory avoids module-level imports of optional dependencies (``redis``,
``fakeredis``) — both backends lazy-import their respective libraries inside
their constructors so production deployments that pick a backend never used
do not need the optional dep installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hecate.core.config import Settings
from hecate.runtime.session_state import InMemorySessionStateStore, SessionStateStore
from hecate.studio.session_state.postgres_store import PostgresSessionStateStore
from hecate.studio.session_state.redis_store import RedisSessionStateStore
from hecate.studio.session_state.tiered_store import TieredSessionStateStore

if TYPE_CHECKING:
    pass


SUPPORTED_BACKENDS = ("memory", "redis", "postgres", "tiered")


def _ttl_seconds_from_settings(settings: Settings) -> int:
    return settings.SESSION_STATE_TTL_DAYS * 86400


def create_session_state_store(settings: Settings) -> SessionStateStore:
    """Select a :class:`SessionStateStore` implementation per ``settings.SESSION_STATE_STORE_BACKEND``.

    Backend selection:
    - ``"memory"`` → :class:`InMemorySessionStateStore` (no external deps)
    - ``"redis"`` → :class:`RedisSessionStateStore` (requires ``redis`` optional dep)
    - ``"postgres"`` → :class:`PostgresSessionStateStore` (uses shared ``async_session_factory``)
    - ``"tiered"`` → :class:`TieredSessionStateStore` composing Redis + PG

    Unknown values raise :class:`ValueError`.
    """
    backend = settings.SESSION_STATE_STORE_BACKEND
    ttl_seconds = _ttl_seconds_from_settings(settings)

    if backend == "memory":
        return InMemorySessionStateStore()

    if backend == "redis":
        if not settings.SESSION_STATE_REDIS_URL:
            raise ValueError("SESSION_STATE_REDIS_URL must be set when SESSION_STATE_STORE_BACKEND='redis'")
        return RedisSessionStateStore(
            redis_url=settings.SESSION_STATE_REDIS_URL,
            key_prefix=settings.SESSION_STATE_KEY_PREFIX,
            ttl_seconds=ttl_seconds,
        )

    if backend == "postgres":
        from hecate.core.database import async_session_factory

        return PostgresSessionStateStore(async_session_factory=async_session_factory)

    if backend == "tiered":
        if not settings.SESSION_STATE_REDIS_URL:
            raise ValueError("SESSION_STATE_REDIS_URL must be set when SESSION_STATE_STORE_BACKEND='tiered'")
        from hecate.core.database import async_session_factory

        redis_store = RedisSessionStateStore(
            redis_url=settings.SESSION_STATE_REDIS_URL,
            key_prefix=settings.SESSION_STATE_KEY_PREFIX,
            ttl_seconds=ttl_seconds,
        )
        postgres_store = PostgresSessionStateStore(async_session_factory=async_session_factory)
        return TieredSessionStateStore(redis_store=redis_store, postgres_store=postgres_store)

    raise ValueError(f"Unsupported SESSION_STATE_STORE_BACKEND={backend!r}; supported values: {SUPPORTED_BACKENDS}")
