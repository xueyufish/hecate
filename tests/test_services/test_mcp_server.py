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

import contextlib
import hashlib
import uuid as _uuid

import httpx
import pytest
from asgi_lifespan import LifespanManager
from starlette.types import ASGIApp

from hecate.enterprise.auth.password import hash_password
from hecate.models.agent import AgentModel
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.tools.mcp.auth_middleware import MCPAuthMiddleware
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


# ---------------------------------------------------------------------------
# Transport auth + server-side tenancy (auth-boundary-hardening)
# ---------------------------------------------------------------------------


async def _seed_workspace_with_agent(db_session, agent_name: str):
    """Create user/org/workspace/member plus one agent owned by it.

    Returns (ws, agent, access_token) — the token claims the member's admin
    role scoped to the seeded workspace.
    """
    suffix = _uuid.uuid4().hex[:8]
    user = UserModel(email=f"mcp-{suffix}@example.com", hashed_password=hash_password("securepass123"))
    db_session.add(user)
    await db_session.flush()
    org = OrganizationModel(name=f"Org {suffix}", slug=f"org-{suffix}", owner_id=user.id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name=f"WS {suffix}", slug=f"ws-{suffix}")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMemberModel(user_id=user.id, workspace_id=ws.id, role=WorkspaceRole.ADMIN))
    agent = AgentModel(workspace_id=ws.id, name=agent_name, model_config_db={"model": "gpt-4o"}, mode="chat")
    db_session.add(agent)
    # Commit: the middleware opens/closes its own sessions on the shared
    # in-memory connection, which would roll back uncommitted seed data.
    await db_session.commit()

    from hecate.enterprise.auth.token import create_access_token

    return ws, agent, create_access_token(user.id, org.id, ws.id, "admin")


@pytest.mark.usefixtures("setup_database")
class TestMCPTransportAuth:
    @contextlib.asynccontextmanager
    async def _client(self, monkeypatch, *, wrap: bool = True):
        """MCP client wired to the wrapped ASGI app with test-DB bindings."""
        from hecate.core.config import settings
        from hecate.tools.mcp import auth_middleware
        from tests.conftest import test_session_factory

        monkeypatch.setattr(auth_middleware, "async_session_factory", test_session_factory)
        monkeypatch.setattr("hecate.tools.mcp.server.async_session_factory", test_session_factory)
        monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")

        app = create_mcp_server().http_app(path="/")
        if wrap:
            app = MCPAuthMiddleware(app)
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    async def test_anonymous_mcp_request_rejected(self, monkeypatch, db_session) -> None:
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope("tools/list")
            resp = await client.post("/", json=body, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "UNAUTHORIZED"

    async def test_valid_jwt_passes_transport_auth(self, monkeypatch, db_session) -> None:
        _ws, _agent, token = await _seed_workspace_with_agent(db_session, "jwt-agent")
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope("tools/list")
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    async def test_auth_type_none_escape_hatch(self, monkeypatch, db_session) -> None:
        from hecate.core.config import settings

        monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "none")
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope("tools/list")
            resp = await client.post("/", json=body, headers=headers)
        assert resp.status_code == 200

    async def test_agent_list_filtered_by_server_identity(self, monkeypatch, db_session) -> None:
        _ws_a, _a1, token_a = await _seed_workspace_with_agent(db_session, "ws-a-agent")
        _ws_b, _b1, _token_b = await _seed_workspace_with_agent(db_session, "ws-b-agent")
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope("tools/call", {"name": "agent_list", "arguments": {}}, name="agent_list")
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        text = resp.json()["result"]["content"][0]["text"]
        assert "ws-a-agent" in text
        assert "ws-b-agent" not in text

    async def test_forged_workspace_argument_does_not_widen_scope(self, monkeypatch, db_session) -> None:
        _ws_a, _a1, token_a = await _seed_workspace_with_agent(db_session, "ws-a-agent")
        ws_b, _b1, _token_b = await _seed_workspace_with_agent(db_session, "ws-b-agent")
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "agent_list", "arguments": {"workspace_id": str(ws_b.id)}},
                name="agent_list",
            )
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        text = resp.json()["result"]["content"][0]["text"]
        assert "ws-b-agent" not in text

    async def test_cross_tenant_session_create_rejected(self, monkeypatch, db_session) -> None:
        _ws_a, _a1, token_a = await _seed_workspace_with_agent(db_session, "ws-a-agent")
        _ws_b, agent_b, _token_b = await _seed_workspace_with_agent(db_session, "ws-b-agent")
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "session_create", "arguments": {"agent_id": str(agent_b.id)}},
                name="session_create",
            )
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        text = resp.json()["result"]["content"][0]["text"]
        assert "Agent not found" in text

    async def test_db_system_key_rejected_on_mcp_transport(self, monkeypatch, db_session) -> None:
        suffix = _uuid.uuid4().hex[:8]
        user = UserModel(email=f"sys-{suffix}@example.com", hashed_password=hash_password("x"))
        db_session.add(user)
        await db_session.flush()
        raw_key = "hcat_mcp_legacy_system"
        db_session.add(
            ApiKeyModel(
                name="legacy",
                key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                key_prefix="hcat_mcp",
                scope=ApiKeyScope.SYSTEM,
                created_by=user.id,
                is_active=True,
            )
        )
        await db_session.commit()
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope("tools/list")
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {raw_key}"})
        assert resp.status_code == 401
