"""FastMCP middleware for the MCP Gateway.

Two hooks carry the whole boundary:

- ``on_list_tools`` — authenticates the caller, merges the federated
  catalog into the first-party listing, and filters the union through
  the policy pipeline (visibility).
- ``on_call_tool`` — authenticates, authorizes, and routes federated
  calls (rest executor / MCP forwarding) before fastmcp's own tools run.

Requests whose caller cannot be authenticated raise :class:`ToolError`,
which surfaces as a JSON-RPC error without executing any tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool, ToolResult

from hecate.core.database import async_session_factory
from hecate.tools.gateway.authz import CallerIdentity, GatewayAuthorizer, resolve_caller_identity
from hecate.tools.gateway.errors import GatewayError
from hecate.tools.gateway.federation import FederatedCatalog

logger = logging.getLogger(__name__)


def _get_tracer() -> Any:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer(__name__)


class _NullSpan:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: Any) -> None:
        return None

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        return None


def _tool_to_dict(tool: Tool) -> dict[str, Any]:
    """First-party Tool → pipeline input dict."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.parameters or {"type": "object", "properties": {}},
        "source": "builtin",
        "risk_level": "low",
    }


class GatewayMiddleware(Middleware):
    """Authenticates, authorizes, and federates at the MCP boundary.

    Args:
        catalog: Federated catalog (rest projections + mcp live proxy).
        authorizer: Policy pipeline boundary authorizer.
    """

    def __init__(self, catalog: FederatedCatalog, authorizer: GatewayAuthorizer) -> None:
        self._catalog = catalog
        self._authorizer = authorizer

    async def _identity(self) -> CallerIdentity:
        headers = get_http_headers()
        try:
            async with async_session_factory() as db:
                return await resolve_caller_identity(db, headers)
        except PermissionError as exc:
            raise ToolError(str(exc)) from exc

    async def on_list_tools(self, context, call_next):
        """Merge federated tools into the first-party listing, then filter."""
        identity = await self._identity()
        first_party: list[Tool] = list(await call_next(context))
        federated = await self._catalog.list_federated_tools(identity.workspace_id, identity.include_platform_federated)
        federated_tools = [
            Tool(
                name=entry["name"],
                description=entry.get("description", ""),
                parameters=entry.get("parameters") or {"type": "object", "properties": {}},
            )
            for entry in federated
        ]
        entries = [(t, _tool_to_dict(t)) for t in first_party] + [
            (t, dict(e)) for t, e in zip(federated_tools, federated, strict=True)
        ]
        visible = self._authorizer.filter_catalog([d for _, d in entries], identity)
        visible_names = {d["name"] for d in visible}
        return [t for t, _ in entries if t.name in visible_names]

    async def on_call_tool(self, context, call_next):
        """Authorize every call; route federated names to their executors."""
        identity = await self._identity()
        name = context.message.name

        federated = await self._catalog.is_federated(name, identity.workspace_id, identity.include_platform_federated)
        if federated:
            allowed, reason = self._authorizer.authorize(
                {"name": name, "source": "rest", "risk_level": "low"}, identity
            )
            if not allowed:
                raise ToolError(f"Tool '{name}' is not available: {reason}")
            tracer = _get_tracer()
            cm = tracer.start_as_current_span("gateway.tool_call") if tracer is not None else _NullSpan()
            with cm as span:
                if span is not None:
                    span.set_attributes(
                        {
                            "gateway.target": name.split("__", 1)[0],
                            "workspace_id": identity.workspace_id or "platform",
                            "tool.name": name,
                            "caller.scope": identity.scope,
                        }
                    )
                try:
                    result = await self._catalog.call_federated_tool(
                        name,
                        dict(context.message.arguments or {}),
                        identity.workspace_id,
                        identity.include_platform_federated,
                    )
                except GatewayError as exc:
                    raise ToolError(str(exc)) from exc
            return ToolResult(content=json.dumps(result) if not isinstance(result, str) else result)

        # First-party tools: authorize with minimal metadata, then delegate.
        allowed, reason = self._authorizer.authorize({"name": name, "source": "builtin", "risk_level": "low"}, identity)
        if not allowed:
            raise ToolError(f"Tool '{name}' is not available: {reason}")
        return await call_next(context)
