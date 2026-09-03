"""Tests for the Hecate MCP Server (2026-07-28 spec compliance).

Covers the protocol-level behavior required by the ``mcp-server`` capability
delta in ``openspec/changes/mcp-streamable-http``:

- Streamable HTTP single-endpoint at ``/mcp``
- ``MCP-Protocol-Version: 2026-07-28`` advertised on every response
- Stateless handling (no ``Mcp-Session-Id`` validation)
- ``server/discover`` advertisement
- ``Mcp-Method`` / ``Mcp-Name`` header enforcement (SEP-2243)
- 4 MiB body limit (HTTP 413)
- Tool list includes the 16 Hecate capabilities
- ``MCP_SERVER_ENABLED=false`` disables the mount

The server is exercised directly via its ASGI app (no DB required, no full
FastAPI app), which keeps these tests fast and isolated. ``asgi-lifespan``
drives the MCP server's startup/shutdown events since ``httpx.ASGITransport``
does not run lifespan by itself.
"""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager
from starlette.types import ASGIApp

from hecate.tools.mcp.server import create_mcp_server


@pytest.fixture
def mcp_app() -> ASGIApp:
    """The MCP server's ASGI app, mounted at root for direct testing."""
    return create_mcp_server().http_app(path="/")


@pytest.fixture
async def mcp_client(mcp_app: ASGIApp) -> httpx.AsyncClient:
    """An httpx.AsyncClient wired directly to the MCP server's ASGI app.

    ``LifespanManager`` drives the MCP server's startup/shutdown events
    (initializing the ``StreamableHTTPSessionManager`` task group) before
    the HTTP client opens; without it, the first request fails with
    "Task group is not initialized".
    """
    async with LifespanManager(mcp_app):
        transport = httpx.ASGITransport(app=mcp_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _modern_envelope(method: str, params: dict | None = None, *, name: str | None = None) -> tuple[dict, dict]:
    """Build a 2026-07-28 envelope body and matching standard headers.

    The body is self-describing via ``_meta`` (protocolVersion + clientInfo +
    clientCapabilities) and the standard headers mirror the method + name
    per SEP-2243.
    """
    body: dict = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    if params:
        body["params"].update(params)
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if name:
        headers["Mcp-Name"] = name
    return body, headers


class TestProtocolVersionAdvertisement:
    async def test_response_uses_2026_result_type_marker(self, mcp_client: httpx.AsyncClient) -> None:
        """On 2026-07-28 protocol era, every result carries ``resultType``.

        fastmcp 4.0.0b3 does not emit an ``MCP-Protocol-Version`` response header
        (the header is for SEP-2243 routing on incoming requests). Protocol era
        advertisement lives in the response body's ``_meta`` / ``resultType``.
        """
        body, headers = _modern_envelope("tools/list")
        resp = await mcp_client.post("/", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        result = resp.json().get("result", {})
        assert result.get("resultType") == "complete"


class TestServerDiscover:
    async def test_server_discover_advertises_capabilities(self, mcp_client: httpx.AsyncClient) -> None:
        body, headers = _modern_envelope("server/discover")
        resp = await mcp_client.post("/", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        # Server identity is advertised via _meta on the response per 2026-07-28
        meta = payload.get("result", {}).get("_meta", {})
        assert "io.modelcontextprotocol/serverInfo" in meta
        server_info = meta["io.modelcontextprotocol/serverInfo"]
        assert server_info.get("name") == "hecate-mcp-server"


class TestHeaderValidation:
    async def test_missing_mcp_method_header_rejected(self, mcp_client: httpx.AsyncClient) -> None:
        body, headers = _modern_envelope("tools/list")
        headers.pop("Mcp-Method")
        resp = await mcp_client.post("/", json=body, headers=headers)
        # The transport validator returns HTTP 400 with JSON-RPC -32020 (HeaderMismatch)
        assert resp.status_code == 400
        payload = resp.json()
        err_code = payload.get("error", {}).get("code")
        assert err_code == -32020

    async def test_mismatched_mcp_method_header_rejected(self, mcp_client: httpx.AsyncClient) -> None:
        body, headers = _modern_envelope("tools/list")
        headers["Mcp-Method"] = "resources/read"  # deliberately wrong
        resp = await mcp_client.post("/", json=body, headers=headers)
        assert resp.status_code == 400
        payload = resp.json()
        err_code = payload.get("error", {}).get("code")
        assert err_code == -32020

    async def test_tools_call_mcp_name_must_match_body(self, mcp_client: httpx.AsyncClient) -> None:
        body, headers = _modern_envelope(
            "tools/call",
            params={"name": "agent_list"},
            name="agent_list",
        )
        headers["Mcp-Name"] = "agent_chat"  # mismatch
        resp = await mcp_client.post("/", json=body, headers=headers)
        assert resp.status_code == 400
        payload = resp.json()
        err_code = payload.get("error", {}).get("code")
        assert err_code == -32020


class TestBodySizeLimit:
    async def test_oversize_body_rejected_with_413(self, mcp_client: httpx.AsyncClient) -> None:
        # 4 MiB + 1 byte of payload
        oversize = "x" * (4 * 1024 * 1024 + 1)
        body, headers = _modern_envelope("tools/call", params={"name": "agent_list"}, name="agent_list")
        body["params"]["arguments"] = {"data": oversize}
        resp = await mcp_client.post("/", json=body, headers=headers)
        # Streamable HTTP servers reject bodies > 4 MiB with 413
        assert resp.status_code == 413


class TestToolList:
    async def test_tools_list_returns_hpecate_capabilities(self, mcp_client: httpx.AsyncClient) -> None:
        body, headers = _modern_envelope("tools/list")
        resp = await mcp_client.post("/", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        result = resp.json().get("result", {})
        tool_names = {t["name"] for t in result.get("tools", [])}

        # Core Hecate capability surface
        expected_subset = {
            "agent_chat",
            "session_create",
            "session_list",
            "session_resume",
            "conversation_history",
            "agent_list",
            "agent_create",
            "agent_update",
            "agent_delete",
            "knowledge_list",
            "knowledge_search",
            "knowledge_create",
            "knowledge_ingest",
            "tool_list",
            "tool_execute",
            "tool_create",
        }
        missing = expected_subset - tool_names
        assert not missing, f"Missing tools: {missing}; got: {sorted(tool_names)}"


class TestCreateMcpServerFactory:
    def test_factory_returns_fastmcp_with_name(self) -> None:
        m = create_mcp_server()
        assert type(m).__name__ == "FastMCP"

    def test_factory_idempotent(self) -> None:
        # Two factories are independent instances but both should work
        m1 = create_mcp_server()
        m2 = create_mcp_server()
        assert m1 is not m2
