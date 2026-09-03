"""Request-scoped tenant context for service-layer wiring.

Provides a contextvar-based carrier for ``(org_id, user_id)`` so that
factories constructing per-worker singletons (e.g.
:class:`~hecate.studio.event_state.PostgresEventStore`) can read the
current request's tenant without threading the tuple through every call
site.

The contextvar is set per-request by API middleware (or directly in tests
via :func:`set_tenant_context`). Service-layer factories capture a closure
over :func:`get_tenant_context` rather than over a specific tuple, so the
same singleton serves all requests.
"""

from __future__ import annotations

import contextvars
import uuid

_tenant_ctx: contextvars.ContextVar[tuple[uuid.UUID, uuid.UUID] | None] = contextvars.ContextVar(
    "hecate_tenant_context", default=None
)


def set_tenant_context(org_id: uuid.UUID, user_id: uuid.UUID) -> contextvars.Token[tuple[uuid.UUID, uuid.UUID] | None]:
    """Bind ``(org_id, user_id)`` to the current async context.

    Returns the :class:`contextvars.Token` for later
    :func:`~contextvars.ContextVar.reset` (typically used in a finally block).
    """
    return _tenant_ctx.set((org_id, user_id))


def get_tenant_context() -> tuple[uuid.UUID, uuid.UUID] | None:
    """Return the current request's ``(org_id, user_id)`` tuple, or ``None``.

    ``None`` is returned outside any request scope (background tasks, tests
    bypassing DI, CLI scripts). Callers SHALL treat ``None`` as "no tenant
    context available" and fall back to nullable tenant columns.
    """
    return _tenant_ctx.get()


def reset_tenant_context(token: contextvars.Token[tuple[uuid.UUID, uuid.UUID] | None]) -> None:
    """Reset the contextvar to its prior state using ``token``."""
    _tenant_ctx.reset(token)
