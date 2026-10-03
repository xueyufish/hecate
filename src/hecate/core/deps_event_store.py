"""FastAPI dependency for accessing the process-wide :class:`EventStore`.

Provides :func:`get_event_store`, the canonical accessor used by API endpoints
that need to append or replay execution events.

The dependency returns the singleton initialised in ``main.py`` lifespan
under ``app.state.event_store`` (also registered process-wide in
``core.composition.entry_assembly``). As a defensive fallback for tests and
code paths that bypass the FastAPI lifespan, the dependency resolves the
shared instance from ``entry_assembly`` when ``app.state`` does not yet
carry the attribute — never a fresh per-call store.
"""

from __future__ import annotations

from fastapi import Request

from hecate.runtime.eventstore import EventStore


def get_event_store(request: Request) -> EventStore:
    """Return the active :class:`EventStore` for this request.

    Resolution order:
    1. ``request.app.state.event_store`` — the singleton set up in
       ``main.py`` lifespan (production path).
    2. Fallback: the process-wide shared store from
       ``core.composition.entry_assembly`` (lazy-built once) — used when
       the lifespan has not run (tests, scripts, ad-hoc invocation), so
       entry paths never split across fresh per-call instances.

    The returned object is always an :class:`EventStore` instance and can be
    used immediately for ``append`` / ``get_events`` / ``replay`` /
    ``get_version`` calls.
    """
    store = getattr(request.app.state, "event_store", None)
    if store is None:
        from hecate.core.composition.entry_assembly import get_shared_event_store

        return get_shared_event_store()
    return store
