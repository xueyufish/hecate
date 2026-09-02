"""FastAPI dependency for accessing the process-wide :class:`SessionStateStore`.

Provides :func:`get_session_state_store`, the canonical accessor used by
API endpoints that need to read or write per-session state.

The dependency returns the singleton initialised in ``main.py`` lifespan
under ``app.state.session_state_store``. As a defensive fallback for tests
and code paths that bypass the FastAPI lifespan, the dependency will call
``create_session_state_store(settings)`` if ``app.state`` does not yet carry
the attribute — yielding a fresh store (which, for the default ``memory``
backend, is a no-op-equivalent :class:`InMemorySessionStateStore`).
"""

from __future__ import annotations

from fastapi import Request

from hecate.core.config import settings
from hecate.runtime.session_state import SessionStateStore
from hecate.services.session_state import create_session_state_store


def get_session_state_store(request: Request) -> SessionStateStore:
    """Return the active :class:`SessionStateStore` for this request.

    Resolution order:
    1. ``request.app.state.session_state_store`` — the singleton set up in
       ``main.py`` lifespan (production path).
    2. Fallback: ``create_session_state_store(settings)`` — used when the
       lifespan has not run (tests, scripts, ad-hoc invocation).

    The returned object is always a :class:`SessionStateStore` instance and
    can be used immediately for ``save`` / ``load`` / ``list_recent`` calls.
    """
    store = getattr(request.app.state, "session_state_store", None)
    if store is None:
        return create_session_state_store(settings)
    return store
