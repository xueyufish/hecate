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
import uuid
from dataclasses import asdict
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

_IDENTITY_HEADER_MAP = {
    "actor": "x-hecate-actor",
    "on_behalf_of": "x-hecate-on-behalf-of",
    "delegation_id": "x-hecate-delegation-id",
    "deployment_id": "x-hecate-deployment",
}


def _identity_headers(identity: CallerIdentity) -> dict[str, str]:
    """Verified service-identity headers for proxied business calls.

    Built exclusively from the server-verified envelope; request-asserted
    values never reach these headers.
    """
    headers: dict[str, str] = {}
    for field, header in _IDENTITY_HEADER_MAP.items():
        value = getattr(identity, field, None)
        if value:
            headers[header] = str(value)
    return headers


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

    @staticmethod
    def _verified_identity_headers(identity: CallerIdentity) -> dict[str, str]:
        return _identity_headers(identity)

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
        """Dispatch first-party and federated calls through the shared authority."""
        from hecate.core.composition.governance import build_platform_governance
        from hecate.tools.policy.governance_adapter import PipelinePolicyProfile

        identity = await self._identity()
        name = context.message.name
        arguments = dict(context.message.arguments or {})
        federated = await self._catalog.is_federated(name, identity.workspace_id, identity.include_platform_federated)
        entries = (
            await self._catalog.list_federated_tools(identity.workspace_id, identity.include_platform_federated)
            if federated
            else []
        )
        metadata = next((dict(entry) for entry in entries if entry["name"] == name), None)
        if metadata is None and context.fastmcp_context is not None:
            tool = await context.fastmcp_context.fastmcp.get_tool(name)
            metadata = _tool_to_dict(tool)
        if metadata is None:
            raise ToolError(f"Tool '{name}' metadata unavailable")
        output_schema = metadata.pop("returns", None)
        envelope = asdict(identity)
        workspace = uuid.UUID(identity.workspace_id) if identity.workspace_id else None
        async with self._catalog.session_factory() as db:
            governance = build_platform_governance(
                db,
                workspace_id=workspace,
                identity=envelope,
                profile=PipelinePolicyProfile(self._authorizer.pipeline, allow_sandbox=False),
            )
            headers = get_http_headers()
            request_id = headers.get("x-hecate-invocation-id") or str(uuid.uuid4())
            call_context = {
                "invocation_id": f"gateway:{identity.actor}:{identity.workspace_id}:{request_id}",
                "tool_metadata": metadata,
                "approval_ref": headers.get("x-hecate-approval-ref"),
            }

            async def dispatch(tool_name, tool_arguments, tool_context):
                if federated:
                    tracer = _get_tracer()
                    cm = tracer.start_as_current_span("gateway.tool_call") if tracer is not None else _NullSpan()
                    try:
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
                            result = await self._catalog.call_federated_tool(
                                tool_name,
                                tool_arguments,
                                identity.workspace_id,
                                identity.include_platform_federated,
                                identity_headers=_identity_headers(identity),
                            )
                    except GatewayError as exc:
                        raise ToolError(str(exc)) from exc
                    if output_schema:
                        from hecate_governance.claims import OutcomeUnknownError
                        from jsonschema import Draft202012Validator

                        if list(Draft202012Validator(output_schema).iter_errors(result)):
                            raise OutcomeUnknownError("federated result violates output schema")
                    return ToolResult(content=json.dumps(result) if not isinstance(result, str) else result).model_dump(
                        mode="json"
                    )
                result = await call_next(context)
                return result.model_dump(mode="json")

            registry = _GatewayDispatch(dispatch)
            try:
                result = await governance.execute_tool(registry, name, arguments, call_context)
            except Exception as exc:
                raise ToolError(str(exc)) from exc
        from mcp.types import CallToolResult

        return ToolResult.from_mcp_result(
            CallToolResult(
                content=result["content"],
                structured_content=result.get("structured_content"),
                is_error=result.get("is_error", False),
                _meta=result.get("meta"),
            )
        )


class _GatewayDispatch:
    def __init__(self, dispatch: Any) -> None:
        self._dispatch = dispatch

    async def execute(self, name: str, arguments: dict, context: dict | None = None) -> Any:
        return await self._dispatch(name, arguments, context)
