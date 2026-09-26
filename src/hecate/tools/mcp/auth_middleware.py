"""Transport-level authentication middleware for the MCP HTTP app.

Wraps the FastMCP ASGI app mounted at ``/mcp``: every request must carry a
bearer credential resolvable through the same provider chain as the REST
API (JWT → database API key → env bootstrap keys). The resolved
:class:`AuthContext` is stashed in ``scope["state"]["auth_context"]`` so
MCP tools can enforce tenancy via ``get_http_request()``.

``MCP_AUTH_TYPE=none`` is an explicit development escape hatch — it skips
authentication and logs a warning at startup (see the ``mcp-server-auth``
spec).
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.core.deps_workspace import authenticate_bearer

logger = logging.getLogger(__name__)

_UNAUTHORIZED_BODY = {
    "detail": {"error": {"code": "UNAUTHORIZED", "message": "Invalid API key or token", "details": None}}
}


def _bearer_token(scope: Scope) -> str | None:
    """Extract the raw bearer token from ASGI request headers."""
    headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


class MCPAuthMiddleware:
    """Pure ASGI middleware enforcing authentication on the MCP mount."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        if settings.MCP_AUTH_TYPE == "none":
            logger.warning(
                "MCP_AUTH_TYPE=none — the /mcp endpoint accepts UNAUTHENTICATED requests. "
                "This escape hatch is for local development only."
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if settings.MCP_AUTH_TYPE == "none":
            await self.app(scope, receive, send)
            return

        token = _bearer_token(scope)
        async with async_session_factory() as db:
            ctx = await authenticate_bearer(token, db)

        if ctx is None:
            # Same state key the REST dependency uses, so the audit
            # middleware observes the failure type (never the credential).
            scope.setdefault("state", {})["auth_failure"] = "invalid_credentials"
            response = JSONResponse(_UNAUTHORIZED_BODY, status_code=401)
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})["auth_context"] = ctx
        await self.app(scope, receive, send)


def get_transport_auth_context() -> AuthContext:
    """Return the caller's AuthContext resolved by the transport middleware.

    Raises:
        PermissionError: When no transport context exists (in-process
            calls without an HTTP request, or an unauthenticated request).
            Tools must never fall back to global permissions.
    """
    from fastmcp.server.dependencies import get_http_request

    request = get_http_request()
    ctx: AuthContext | None = getattr(request.state, "auth_context", None)
    if ctx is None:
        raise PermissionError("unauthenticated: MCP transport did not provide an identity")
    return ctx
