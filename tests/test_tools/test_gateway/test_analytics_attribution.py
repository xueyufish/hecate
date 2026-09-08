"""Attribution tests: gateway calls carry workspace/target into spans."""

from __future__ import annotations

import json
import uuid

from fastmcp import Client, FastMCP
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from hecate.tools.gateway.authz import CallerIdentity, GatewayAuthorizer
from hecate.tools.gateway.federation import FederatedCatalog
from hecate.tools.gateway.middleware import GatewayMiddleware


class StubMCPManager:
    async def discover_tools(self, server_name: str) -> list[dict]:
        return [{"name": "create_issue", "description": "CI", "inputSchema": {"type": "object"}}]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        return {"upstream": server_name}


async def test_federated_call_emits_attributed_span(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("gateway-test")
    monkeypatch.setattr("hecate.tools.gateway.middleware._get_tracer", lambda: tracer)

    ws = uuid.uuid4()
    from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
    from hecate.tools.gateway.targets import GatewayTargetService
    from tests.conftest import test_session_factory as factory

    async with factory() as session:
        await GatewayTargetService(session).create_target(
            GatewayTargetCreateSchema(
                name="github",
                kind=GatewayTargetKind.MCP,
                base_url="https://mcp.example.com",
                workspace_id=ws,
            )
        )
        await session.commit()

    mcp = FastMCP("test-gateway")

    @mcp.tool
    def echo(text: str) -> str:
        """Echo."""
        return text

    catalog = FederatedCatalog(db_session_factory=factory, mcp_manager=StubMCPManager())
    mcp.add_middleware(GatewayMiddleware(catalog, GatewayAuthorizer()))

    async def fixed_identity(self) -> CallerIdentity:
        return CallerIdentity(workspace_id=str(ws), scope="workspace", authenticated=True)

    monkeypatch.setattr(GatewayMiddleware, "_identity", fixed_identity)

    async with Client(mcp) as client:
        result = await client.call_tool("github__create_issue", {"title": "x"})
    assert json.loads(result.content[0].text) == {"upstream": "github"}

    spans = [s for s in exporter.get_finished_spans() if s.name == "gateway.tool_call"]
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs["gateway.target"] == "github"
    assert attrs["workspace_id"] == str(ws)
    assert attrs["tool.name"] == "github__create_issue"
    assert attrs["caller.scope"] == "workspace"
