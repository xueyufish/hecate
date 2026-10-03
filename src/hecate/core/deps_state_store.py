"""FastAPI dependency for accessing the process-wide :class:`SessionStateStore`.

Provides :func:`get_session_state_store`, the canonical accessor used by
API endpoints that need to read or write per-session state.

The dependency returns the singleton initialised in ``main.py`` lifespan
under ``app.state.session_state_store`` (also registered process-wide in
``core.composition.entry_assembly``). As a defensive fallback for tests
and code paths that bypass the FastAPI lifespan, the dependency resolves
the shared instance from ``entry_assembly`` when ``app.state`` does not
yet carry the attribute — never a fresh per-call store.
"""

from __future__ import annotations

from fastapi import Request

from hecate.runtime.session_state import SessionStateStore


def get_session_state_store(request: Request) -> SessionStateStore:
    """Return the active :class:`SessionStateStore` for this request.

    Resolution order:
    1. ``request.app.state.session_state_store`` — the singleton set up in
       ``main.py`` lifespan (production path).
    2. Fallback: the process-wide shared store from
       ``core.composition.entry_assembly`` (lazy-built once) — used when
       the lifespan has not run (tests, scripts, ad-hoc invocation).

    The returned object is always a :class:`SessionStateStore` instance and
    can be used immediately for ``save`` / ``load`` / ``list_recent`` calls.
    """
    store = getattr(request.app.state, "session_state_store", None)
    if store is None:
        from hecate.core.composition.entry_assembly import get_shared_session_state_store

        return get_shared_session_state_store()
    return store
