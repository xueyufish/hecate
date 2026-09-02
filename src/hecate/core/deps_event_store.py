"""FastAPI dependency for accessing the process-wide :class:`EventStore`.

Provides :func:`get_event_store`, the canonical accessor used by API endpoints
that need to append or replay execution events.

The dependency returns the singleton initialised in ``main.py`` lifespan
under ``app.state.event_store``. As a defensive fallback for tests and code
paths that bypass the FastAPI lifespan, the dependency will call
``create_event_store(settings)`` if ``app.state`` does not yet carry the
attribute — yielding a fresh store (which, for the default ``memory``
backend, is an :class:`InMemoryEventStore`).
"""

from __future__ import annotations

from fastapi import Request

from hecate.core.config import settings
from hecate.runtime.eventstore import EventStore
from hecate.services.event_state import create_event_store


def get_event_store(request: Request) -> EventStore:
    """Return the active :class:`EventStore` for this request.

    Resolution order:
    1. ``request.app.state.event_store`` — the singleton set up in
       ``main.py`` lifespan (production path).
    2. Fallback: ``create_event_store(settings)`` — used when the lifespan
       has not run (tests, scripts, ad-hoc invocation).

    The returned object is always an :class:`EventStore` instance and can be
    used immediately for ``append`` / ``get_events`` / ``replay`` /
    ``get_version`` calls.
    """
    store = getattr(request.app.state, "event_store", None)
    if store is None:
        return create_event_store(settings)
    return store
