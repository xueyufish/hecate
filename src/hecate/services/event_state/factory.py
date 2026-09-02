"""Factory for selecting an EventStore backend at application startup.

Provides :func:`create_event_store` that selects an implementation based on
``settings.EVENT_STORE_BACKEND``. Supported values:

- ``"memory"`` — :class:`hecate.runtime.eventstore.InMemoryEventStore`
  (single-process, default for backward compatibility).
- ``"postgres"`` — :class:`PostgresEventStore` (durable append-only log,
  recommended for production).

Unknown values raise :class:`ValueError`.
"""

from __future__ import annotations

from hecate.core.config import Settings
from hecate.core.request_context import get_tenant_context
from hecate.runtime.eventstore import EventStore, InMemoryEventStore
from hecate.services.event_state.postgres_store import PostgresEventStore

SUPPORTED_BACKENDS = ("memory", "postgres")


def create_event_store(settings: Settings) -> EventStore:
    """Select an :class:`EventStore` implementation per ``settings.EVENT_STORE_BACKEND``.

    Backend selection:
    - ``"memory"`` → :class:`InMemoryEventStore` (no external deps)
    - ``"postgres"`` → :class:`PostgresEventStore` (uses shared
      ``async_session_factory`` + ``tenant_context_provider`` closure over
      :func:`hecate.core.request_context.get_tenant_context`)

    Unknown values raise :class:`ValueError`.
    """
    backend = settings.EVENT_STORE_BACKEND

    if backend == "memory":
        return InMemoryEventStore()

    if backend == "postgres":
        from hecate.core.database import async_session_factory

        return PostgresEventStore(
            async_session_factory=async_session_factory,
            tenant_context_provider=get_tenant_context,
        )

    raise ValueError(f"Unsupported EVENT_STORE_BACKEND={backend!r}; supported values: {SUPPORTED_BACKENDS}")
