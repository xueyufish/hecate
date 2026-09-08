"""Tests for federated catalog: listing, naming, call routing, isolation."""

from __future__ import annotations

import uuid

import httpx
import pytest

from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import GatewayError
from hecate.tools.gateway.federation import (
    FederatedCatalog,
    federated_tool_name,
    resolve_federated_name,
)
from hecate.tools.gateway.targets import GatewayTargetService
from tests.conftest import test_session_factory as _session_factory


class StubMCPManager:
    """Minimal MCPClientManager double for catalog tests."""

    def __init__(self, tools_per_server: dict[str, list[dict]] | None = None) -> None:
        self._tools = tools_per_server or {}
        self.forwarded: list[tuple[str, str, dict]] = []

    async def discover_tools(self, server_name: str) -> list[dict]:
        if server_name == "broken":
            raise ConnectionError("circuit open")
        return self._tools.get(server_name, [])

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        self.forwarded.append((server_name, tool_name, arguments))
        return {"server": server_name, "tool": tool_name}


async def _make_target(session, name: str, kind: GatewayTargetKind, workspace_id=None):
    service = GatewayTargetService(session)
    return await service.create_target(
        GatewayTargetCreateSchema(
            name=name,
            kind=kind,
            base_url="https://api.example.com" if kind == GatewayTargetKind.REST else "https://mcp.example.com",
            spec={"openapi": "3.0.0", "paths": {"/ping": {"get": {"operationId": "ping"}}}}
            if kind == GatewayTargetKind.REST
            else None,
            workspace_id=workspace_id,
        )
    )


class TestPresentationNaming:
    def test_federated_name_format(self) -> None:
        assert federated_tool_name("github", "create_issue") == "github__create_issue"

    def test_resolve_longest_prefix(self) -> None:
        targets = ["a", "a__b", "github"]
        assert resolve_federated_name("github__create_issue", targets) == ("github", "create_issue")
        assert resolve_federated_name("a__b__c", targets) == ("a__b", "c")

    def test_resolve_unknown_prefix(self) -> None:
        assert resolve_federated_name("unknown__tool", ["github"]) is None

    def test_resolve_bare_name_not_federated(self) -> None:
        assert resolve_federated_name("github", ["github"]) is None


class TestCatalog:
    async def test_rest_projection_listed_and_callable(self) -> None:
        ws = uuid.uuid4()
        async with _session_factory() as session:
            target = await _make_target(session, "crm", GatewayTargetKind.REST, workspace_id=ws)
            session.add(
                ToolModel(
                    workspace_id=ws,
                    name="crm__ping",
                    description="GET /ping",
                    source="rest",
                    parameters={"type": "object", "properties": {}, "x-gateway": {}},
                    target_id=target.id,
                )
            )
            await session.commit()

        catalog = FederatedCatalog(db_session_factory=_session_factory)
        tools = await catalog.list_federated_tools(str(ws), include_platform=False)
        assert [t["name"] for t in tools] == ["crm__ping"]
        assert "x-gateway" not in tools[0]["parameters"]
        assert await catalog.is_federated("crm__ping", str(ws), include_platform=False)

    async def test_workspace_isolation(self) -> None:
        ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
        async with _session_factory() as session:
            target = await _make_target(session, "crm", GatewayTargetKind.REST, workspace_id=ws_a)
            session.add(
                ToolModel(
                    workspace_id=ws_a,
                    name="crm__ping",
                    description="GET /ping",
                    source="rest",
                    parameters={},
                    target_id=target.id,
                )
            )
            await session.commit()

        catalog = FederatedCatalog(db_session_factory=_session_factory)
        tools_b = await catalog.list_federated_tools(str(ws_b), include_platform=False)
        assert tools_b == []
        assert not await catalog.is_federated("crm__ping", str(ws_b), include_platform=False)

    async def test_platform_target_only_for_system_scope(self) -> None:
        async with _session_factory() as session:
            target = await _make_target(session, "crm", GatewayTargetKind.REST, workspace_id=None)
            session.add(
                ToolModel(
                    workspace_id=uuid.UUID(int=0),
                    name="crm__ping",
                    description="GET /ping",
                    source="rest",
                    parameters={},
                    target_id=target.id,
                )
            )
            await session.commit()

        catalog = FederatedCatalog(db_session_factory=_session_factory)
        ws = uuid.uuid4()
        assert await catalog.list_federated_tools(str(ws), include_platform=False) == []
        platform_view = await catalog.list_federated_tools(None, include_platform=True)
        assert [t["name"] for t in platform_view] == ["crm__ping"]

    async def test_mcp_listing_prefixed_and_resilient(self) -> None:
        ws = uuid.uuid4()
        async with _session_factory() as session:
            await _make_target(session, "github", GatewayTargetKind.MCP, workspace_id=ws)
            await _make_target(session, "broken", GatewayTargetKind.MCP, workspace_id=ws)
            await session.commit()

        manager = StubMCPManager(
            {
                "github": [
                    {"name": "create_issue", "description": "Create", "inputSchema": {"type": "object"}},
                    {"name": "close_issue", "description": "Close", "inputSchema": {"type": "object"}},
                ]
            }
        )
        catalog = FederatedCatalog(db_session_factory=_session_factory, mcp_manager=manager)
        tools = await catalog.list_federated_tools(str(ws), include_platform=False)
        assert [t["name"] for t in tools] == [
            "github__close_issue",
            "github__create_issue",
        ]

    async def test_mcp_cross_target_no_collision(self) -> None:
        ws = uuid.uuid4()
        async with _session_factory() as session:
            await _make_target(session, "github", GatewayTargetKind.MCP, workspace_id=ws)
            await _make_target(session, "gitlab", GatewayTargetKind.MCP, workspace_id=ws)
            await session.commit()

        shared = [{"name": "create_issue", "description": "", "inputSchema": {"type": "object"}}]
        manager = StubMCPManager({"github": shared, "gitlab": shared})
        catalog = FederatedCatalog(db_session_factory=_session_factory, mcp_manager=manager)
        tools = await catalog.list_federated_tools(str(ws), include_platform=False)
        assert sorted(t["name"] for t in tools) == [
            "github__create_issue",
            "gitlab__create_issue",
        ]

    async def test_mcp_call_forwards_through_manager(self) -> None:
        ws = uuid.uuid4()
        async with _session_factory() as session:
            await _make_target(session, "github", GatewayTargetKind.MCP, workspace_id=ws)
            await session.commit()

        manager = StubMCPManager({"github": []})
        catalog = FederatedCatalog(db_session_factory=_session_factory, mcp_manager=manager)
        result = await catalog.call_federated_tool(
            "github__create_issue", {"title": "hi"}, str(ws), include_platform=False
        )
        assert manager.forwarded == [("github", "create_issue", {"title": "hi"})]
        assert result == {"server": "github", "tool": "create_issue"}

    async def test_unknown_federated_name_rejected(self) -> None:
        ws = uuid.uuid4()
        async with _session_factory() as session:
            await _make_target(session, "github", GatewayTargetKind.MCP, workspace_id=ws)
            await session.commit()

        catalog = FederatedCatalog(db_session_factory=_session_factory, mcp_manager=StubMCPManager())
        with pytest.raises(GatewayError, match="Unknown federated tool"):
            await catalog.call_federated_tool("unknown__tool", {}, str(ws), include_platform=False)

    async def test_rest_call_routes_to_executor(self) -> None:
        from hecate.tools.gateway.executor import RestToolExecutor

        ws = uuid.uuid4()
        async with _session_factory() as session:
            target = await _make_target(session, "crm", GatewayTargetKind.REST, workspace_id=ws)
            session.add(
                ToolModel(
                    workspace_id=ws,
                    name="crm__ping",
                    description="GET /ping",
                    source="rest",
                    parameters={
                        "type": "object",
                        "properties": {},
                        "x-gateway": {
                            "method": "GET",
                            "path": "/ping",
                            "path_params": [],
                            "query_params": [],
                            "body_params": [],
                        },
                    },
                    target_id=target.id,
                )
            )
            await session.commit()

        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        catalog = FederatedCatalog(
            db_session_factory=_session_factory,
            executor=RestToolExecutor(client=client),
        )
        result = await catalog.call_federated_tool("crm__ping", {}, str(ws), include_platform=False)
        assert result == {"ok": True}


class RecordingHandler:
    def __init__(self) -> None:
        self.requests: list = []

    def __call__(self, request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})


class TestTargetServiceMCPRegistration:
    async def test_mcp_target_registers_with_manager_on_create(self) -> None:
        class StubRegistry:
            def __init__(self) -> None:
                self.registered: dict[str, str] = {}

            def unregister(self, name: str) -> None:
                self.registered.pop(name, None)

        class StubManager:
            def __init__(self) -> None:
                self.registry = StubRegistry()

            def register_server(self, name: str, endpoint: str, **kwargs) -> None:
                self.registry.registered[name] = endpoint

        async with _session_factory() as session:
            manager = StubManager()
            service = GatewayTargetService(session, mcp_manager=manager)
            await service.create_target(
                GatewayTargetCreateSchema(
                    name="github",
                    kind=GatewayTargetKind.MCP,
                    base_url="https://mcp.example.com",
                )
            )
            assert manager.registry.registered["github"] == "https://mcp.example.com"

    async def test_deactivated_mcp_target_unregisters(self) -> None:
        class StubRegistry:
            def __init__(self) -> None:
                self.registered: set[str] = set()

            def unregister(self, name: str) -> None:
                self.registered.discard(name)

        class StubManager:
            def __init__(self) -> None:
                self.registry = StubRegistry()

            def register_server(self, name: str, endpoint: str, **kwargs) -> None:
                self.registry.registered.add(name)

        async with _session_factory() as session:
            manager = StubManager()
            service = GatewayTargetService(session, mcp_manager=manager)
            target = await service.create_target(
                GatewayTargetCreateSchema(
                    name="github",
                    kind=GatewayTargetKind.MCP,
                    base_url="https://mcp.example.com",
                )
            )
            assert "github" in manager.registry.registered
            await service.deactivate_target(target.id)
            assert "github" not in manager.registry.registered
