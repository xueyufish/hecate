"""HTTP middleware for automatic audit logging.

The :class:`AuditMiddleware` captures every HTTP request/response cycle,
extracts authentication context, and enqueues an :class:`AuditEvent` for
async batch persistence by the :class:`AuditBatchWriter`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from hecate.services.audit.store import AuditEvent

logger = logging.getLogger(__name__)

# Paths that should NOT generate audit events.
_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)

# Module-level queue reference — set by AuditBatchWriter during lifespan startup.
# The middleware reads from this; the writer drains from it.
_audit_queue: asyncio.Queue[AuditEvent] | None = None


def set_audit_queue(queue: asyncio.Queue[AuditEvent]) -> None:
    """Set the global audit queue reference.

    Called once during application lifespan startup.
    """
    global _audit_queue  # noqa: PLW0603
    _audit_queue = queue


def get_audit_queue() -> asyncio.Queue[AuditEvent] | None:
    """Return the global audit queue, or None if not initialized."""
    return _audit_queue


def _map_path_to_action(path: str, method: str) -> str:
    """Derive a best-effort audit action from request path and method.

    Falls back to ``api.<method>`` for unrecognized paths.
    """
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "api":
        resource = parts[1]
        if method == "POST":
            return f"api.{resource}.create"
        if method == "PUT" or method == "PATCH":
            return f"api.{resource}.update"
        if method == "DELETE":
            return f"api.{resource}.delete"
    return f"api.{method.lower()}"


class AuditMiddleware(BaseHTTPMiddleware):
    """Capture all HTTP request/response cycles as audit events.

    Skips excluded paths (health, metrics, docs) and OPTIONS requests.
    Enqueues events into the global audit queue for batch persistence.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> StarletteResponse:
        # Skip excluded paths and CORS preflight
        if request.url.path in _EXCLUDED_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        response = await call_next(request)

        # Try to enqueue audit event
        queue = get_audit_queue()
        if queue is None:
            return response

        try:
            # Extract auth context from request state (set by auth dependency)
            ctx = getattr(request.state, "auth_context", None)

            user_id: uuid.UUID
            org_id: uuid.UUID
            workspace_id: uuid.UUID | None = None

            if ctx is not None:
                user_id = ctx.user_id
                org_id = ctx.org_id or uuid.UUID(int=0)
                workspace_id = ctx.workspace_id
            else:
                # Unauthenticated request — use sentinel values
                user_id = uuid.UUID(int=0)
                org_id = uuid.UUID(int=0)

            event = AuditEvent(
                org_id=org_id,
                user_id=user_id,
                workspace_id=workspace_id,
                action=_map_path_to_action(request.url.path, request.method),
                resource_type=None,
                resource_id=None,
                request_method=request.method,
                request_path=request.url.path,
                response_status=response.status_code,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                success=200 <= response.status_code < 400,
            )
            # Non-blocking put — if queue is full, drop the event rather than blocking
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Audit queue full — dropping event for %s %s", request.method, request.url.path)

        except Exception as e:
            logger.error("Failed to create audit event: %s", e)

        return response
