"""Middleware-level tests: listing merge, federated routing, enforcement."""

from __future__ import annotations

import json
import uuid

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from hecate.core.config import settings
from hecate.tools.gateway.authz import CallerIdentity, GatewayAuthorizer
from hecate.tools.gateway.federation import FederatedCatalog
from hecate.tools.gateway.middleware import GatewayMiddleware
from hecate.tools.policy import PolicyDecision, ToolPolicyPipeline
from hecate.tools.policy.policy_pipeline import PolicyContext, PolicyLayer, ToolInfo

_OBJ = {"type": "object"}


class StubMCPManager:
    def __init__(self, tools_per_server: dict[str, list[dict]] | None = None) -> None:
        self._tools = tools_per_server or {}

    async def discover_tools(self, server_name: str) -> list[dict]:
        return self._tools.get(server_name, [])

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        return {"upstream": server_name, "tool": tool_name}


def _build_server(workspace_id: uuid.UUID, tools_per_server: dict[str, list[dict]]) -> FastMCP:
    mcp = FastMCP("test-gateway")

    @mcp.tool
    def echo(text: str) -> str:
        """Echo the text back."""
        return f"echo: {text}"

    from tests.conftest import test_session_factory as factory

    catalog = FederatedCatalog(
        db_session_factory=factory,
        mcp_manager=StubMCPManager(tools_per_server),
    )
    mcp.add_middleware(GatewayMiddleware(catalog, GatewayAuthorizer()))
    return mcp


@pytest.fixture
def patch_identity(monkeypatch):
    """Make the middleware resolve a fixed workspace identity."""

    def _apply(server: FastMCP, identity: CallerIdentity) -> None:
        async def fixed(self) -> CallerIdentity:
            return identity

        monkeypatch.setattr(GatewayMiddleware, "_identity", fixed)

    return _apply


async def _seed_mcp_target(workspace_id: uuid.UUID, name: str = "github") -> None:
    from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
    from hecate.tools.gateway.targets import GatewayTargetService
    from tests.conftest import test_session_factory as factory

    async with factory() as session:
        await GatewayTargetService(session).create_target(
            GatewayTargetCreateSchema(
                name=name,
                kind=GatewayTargetKind.MCP,
                base_url="https://mcp.example.com",
                workspace_id=workspace_id,
            )
        )
        await session.commit()


async def test_listing_merges_first_party_and_federated(patch_identity) -> None:
    ws = uuid.uuid4()
    await _seed_mcp_target(ws, "github")
    server = _build_server(ws, {"github": [{"name": "create_issue", "description": "CI", "inputSchema": _OBJ}]})
    patch_identity(server, CallerIdentity(workspace_id=str(ws), scope="workspace", authenticated=True))
    async with Client(server) as client:
        tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "echo" in names
    assert "github__create_issue" in names


async def test_platform_scope_sees_first_party_only(patch_identity) -> None:
    ws = uuid.uuid4()
    await _seed_mcp_target(ws, "github")
    server = _build_server(ws, {"github": [{"name": "create_issue", "description": "CI", "inputSchema": _OBJ}]})
    patch_identity(server, CallerIdentity(workspace_id=None, scope="platform", authenticated=True))
    async with Client(server) as client:
        tools = await client.list_tools()
    names = [t.name for t in tools]
    assert "echo" in names
    assert "github__create_issue" not in names


async def test_federated_call_routes_to_upstream(patch_identity) -> None:
    ws = uuid.uuid4()
    await _seed_mcp_target(ws, "github")
    server = _build_server(ws, {"github": [{"name": "create_issue", "description": "CI", "inputSchema": _OBJ}]})
    patch_identity(server, CallerIdentity(workspace_id=str(ws), scope="workspace", authenticated=True))
    async with Client(server) as client:
        result = await client.call_tool("github__create_issue", {"title": "x"})
    payload = json.loads(result.content[0].text)
    assert payload == {"upstream": "github", "tool": "create_issue"}


async def test_denied_federated_call_raises(patch_identity) -> None:
    ws = uuid.uuid4()
    await _seed_mcp_target(ws, "github")
    server = _build_server(ws, {"github": [{"name": "create_issue", "description": "CI", "inputSchema": _OBJ}]})
    patch_identity(server, CallerIdentity(workspace_id=str(ws), scope="workspace", authenticated=True))

    class DenyAll(PolicyLayer):
        @property
        def name(self) -> str:
            return "deny-all"

        def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
            return PolicyDecision.DENY

    gateway_mw = next(m for m in server.middleware if isinstance(m, GatewayMiddleware))
    gateway_mw._authorizer = GatewayAuthorizer(pipeline=ToolPolicyPipeline(layers=[DenyAll()]))
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool("github__create_issue", {"title": "x"})


async def test_unauthenticated_listing_rejected(monkeypatch) -> None:
    ws = uuid.uuid4()
    server = _build_server(ws, {})
    monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "api_key")
    monkeypatch.setattr(settings, "HECATE_API_KEYS", "")
    from mcp.shared.exceptions import MCPError

    async with Client(server) as client:
        # call_tool errors surface as ToolError; list_tools errors are
        # wrapped into a protocol-level MCPError by the server.
        with pytest.raises((ToolError, MCPError)):
            await client.list_tools()
