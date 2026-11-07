"""REST target and account boundaries through real loopback HTTP requests."""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
from hecate.tools.gateway.authz import CallerIdentity, GatewayAuthorizer
from hecate.tools.gateway.converter import project_target_tools
from hecate.tools.gateway.errors import GatewayError, RestExecutionError
from hecate.tools.gateway.executor import RestToolExecutor
from hecate.tools.gateway.federation import FederatedCatalog
from hecate.tools.gateway.middleware import GatewayMiddleware
from hecate.tools.gateway.targets import GatewayTargetService
from hecate.tools.policy import ToolPolicyPipeline
from hecate.tools.policy.policy_layers import ProfileLayer, VisibilityLayer
from hecate.tools.tool.builtin import BuiltInToolExecutor
from hecate.tools.tool.registry import ToolRegistry

WORKSPACE = uuid.UUID(int=0)


class StubBusinessHandler(BaseHTTPRequestHandler):
    """Accept one synthetic account and record every received HTTP request."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:
        """Keep synthetic credential headers out of test logs."""

    def do_GET(self) -> None:  # noqa: N802 - HTTP server callback
        """Serve a read, explicit account refusal or cross-origin redirect."""
        server = cast(StubBusinessServer, self.server)
        server.requests.append({"path": self.path, "headers": dict(self.headers)})
        if self.path == "/v2/redirect":
            self.send_response(302)
            self.send_header("Location", server.redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        allowed = self.path.startswith("/v2/accounts/account-a/")
        payload = json.dumps({"ok": allowed, "account": "account-a" if allowed else "account-b"}).encode()
        self.send_response(200 if allowed else 403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class StubBusinessServer(ThreadingHTTPServer):
    """One local business receiver with independently inspectable requests."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.redirect_target = ""
        super().__init__(("127.0.0.1", 0), StubBusinessHandler)

    @property
    def url(self) -> str:
        """The actual listening origin."""
        return f"http://127.0.0.1:{self.server_port}"


@pytest.fixture
def upstreams():
    first, second = StubBusinessServer(), StubBusinessServer()
    first.redirect_target = second.url + "/receive"
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (first, second)]
    for thread in threads:
        thread.start()
    try:
        yield first, second
    finally:
        for server in (first, second):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.fixture
async def rest_target(db_session, upstreams):
    first, second = upstreams
    target = await GatewayTargetService(db_session).create_target(
        GatewayTargetCreateSchema(
            name="crm",
            kind=GatewayTargetKind.REST,
            workspace_id=WORKSPACE,
            base_url=first.url + "/v2",
            credentials={"headers": {"X-Api-Key": "synthetic-test-key", "X-HeCate-Actor": "credential-forged"}},
            spec={
                "openapi": "3.0.0",
                "servers": [{"url": second.url}],
                "paths": {
                    "/accounts/{account_id}/contacts/{contact_id}": {
                        "get": {
                            "operationId": "read",
                            "parameters": [
                                {
                                    "name": "account_id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string", "enum": ["account-a", "account-b"]},
                                },
                                {"name": "contact_id", "in": "path", "required": True, "schema": {"type": "string"}},
                            ],
                        }
                    },
                    "/redirect": {"get": {"operationId": "redirect"}},
                },
            },
        )
    )
    report = await project_target_tools(db_session, target)
    assert set(report.imported) == {"crm__read", "crm__redirect"}
    await db_session.commit()
    return target


def _gateway(monkeypatch, executor, *, deny_other_account: bool = False):
    from tests.conftest import test_session_factory

    async def identity(self):
        return CallerIdentity(
            workspace_id=str(WORKSPACE), scope="workspace", authenticated=True, actor="verified-reader"
        )

    monkeypatch.setattr(GatewayMiddleware, "_identity", identity)
    rules = [{"action": "deny", "tool_pattern": "crm__read", "arg_conditions": {"account_id": "account-b"}}]
    pipeline = ToolPolicyPipeline(layers=[ProfileLayer(rules=rules if deny_other_account else []), VisibilityLayer()])
    catalog = FederatedCatalog(db_session_factory=test_session_factory, executor=executor)
    server = FastMCP("rest-boundary-test")
    server.add_middleware(GatewayMiddleware(catalog, GatewayAuthorizer(pipeline=pipeline)))
    return server


@pytest.mark.parametrize("entry", ["registry", "gateway"])
async def test_normal_entry_points_contain_real_http_targets(db_session, rest_target, upstreams, monkeypatch, entry):
    first, second = upstreams
    async with httpx.AsyncClient(
        timeout=3, follow_redirects=True, headers={"X-HeCate-Actor": "client-forged"}
    ) as client:
        executor = RestToolExecutor(client=client)
        registry = ToolRegistry(
            db=db_session,
            workspace_id=WORKSPACE,
            builtin_executor=BuiltInToolExecutor(search_provider=None, workspace_root="~/workspaces"),
            rest_executor=executor,
        )
        async with Client(_gateway(monkeypatch, executor)) as gateway:

            async def execute(name, arguments):
                if entry == "registry":
                    return await registry.execute(name, arguments)
                result = await gateway.call_tool(name, arguments)
                return json.loads(result.content[0].text)

            result = await execute("crm__read", {"account_id": "account-a", "contact_id": "42"})
            assert result == {"ok": True, "account": "account-a"}
            assert len(first.requests) == 1
            headers = httpx.Headers(first.requests[0]["headers"])
            assert headers["x-api-key"] == "synthetic-test-key"
            assert headers.get("x-hecate-actor") == ("verified-reader" if entry == "gateway" else None)
            with pytest.raises((GatewayError, ToolError)):
                await execute("crm__read", {"account_id": "account-a", "contact_id": "../admin"})
            assert len(first.requests) == 1
            with pytest.raises((RestExecutionError, ToolError)):
                await execute("crm__redirect", {})
            assert len(first.requests) == 2
            assert not second.requests
        assert not client.is_closed and client.follow_redirects
    print({"entry": entry, "allowed_requests": 1, "path_escape_requests": 0, "redirect_followups": 0})


async def test_account_policy_and_upstream_refusal_are_distinct(db_session, rest_target, upstreams, monkeypatch):
    first, second = upstreams
    executor = RestToolExecutor(timeout=3)
    async with Client(_gateway(monkeypatch, executor, deny_other_account=True)) as gateway:
        await gateway.call_tool("crm__read", {"account_id": "account-a", "contact_id": "42"})
        with pytest.raises(ToolError):
            await gateway.call_tool("crm__read", {"account_id": "account-b", "contact_id": "42"})
        assert len(first.requests) == 1
    registry = ToolRegistry(
        db=db_session,
        workspace_id=WORKSPACE,
        builtin_executor=BuiltInToolExecutor(search_provider=None, workspace_root="~/workspaces"),
        rest_executor=executor,
    )
    with pytest.raises(RestExecutionError) as error:
        await registry.execute("crm__read", {"account_id": "account-b", "contact_id": "42"})
    assert error.value.status_code == 403 and error.value.failure_class == "upstream_error"
    assert len(first.requests) == 2
    assert not second.requests
    print({"account_a_success": 1, "policy_denied_before_http": 1, "upstream_403": 1, "redirect_followups": 0})
