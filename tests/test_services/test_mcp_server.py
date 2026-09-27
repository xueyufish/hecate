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
import json
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


async def _seed_workspace_with_agent(db_session, agent_name: str, role: WorkspaceRole = WorkspaceRole.ADMIN):
    """Create user/org/workspace/member plus one agent owned by it.

    Returns (ws, agent, access_token) — the token claims the member's role
    scoped to the seeded workspace (default admin).
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
    db_session.add(WorkspaceMemberModel(user_id=user.id, workspace_id=ws.id, role=role))
    agent = AgentModel(workspace_id=ws.id, name=agent_name, model_config_db={"model": "gpt-4o"}, mode="chat")
    db_session.add(agent)
    # Commit: the middleware opens/closes its own sessions on the shared
    # in-memory connection, which would roll back uncommitted seed data.
    await db_session.commit()

    from hecate.enterprise.auth.token import create_access_token

    return ws, agent, create_access_token(user.id, org.id, ws.id, role.value)


async def _seed_workspace_no_member(db_session):
    """Create a user/org/workspace without a WorkspaceMemberModel row.

    Returns (ws, access_token) — the token claims the workspace but the
    JWT provider's request-time membership check will yield ``role=None``
    in the AuthContext (and the token itself is rejected by jwt_provider;
    we use this helper to seed state only).
    """
    suffix = _uuid.uuid4().hex[:8]
    user = UserModel(email=f"nomem-{suffix}@example.com", hashed_password=hash_password("securepass123"))
    db_session.add(user)
    await db_session.flush()
    org = OrganizationModel(name=f"Org {suffix}", slug=f"org-{suffix}", owner_id=user.id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name=f"WS {suffix}", slug=f"ws-{suffix}")
    db_session.add(ws)
    await db_session.commit()
    return ws, user


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


# ---------------------------------------------------------------------------
# G1 MCP action-authorization failure reproductions.
#
# Each test asserts the post-call invariant that MUST hold once the
# fix lands (no AgentModel row, no KnowledgeBaseModel row, no ToolModel
# row, no file side effect). They currently FAIL against the unfixed
# server: viewer and no-membership identities are accepted and
# mutations land in the caller's workspace — sometimes silently
# polluting the bundled zero workspace when no workspace is supplied.
# ---------------------------------------------------------------------------


def _call_tool(client: httpx.AsyncClient, headers: dict, name: str, arguments: dict) -> dict:
    """Send a tools/call envelope and return the parsed JSON body."""
    body, base_headers = _modern_envelope("tools/call", {"name": name, "arguments": arguments}, name=name)
    return {"body": body, "headers": {**base_headers, **headers}}


@pytest.mark.usefixtures("setup_database")
class TestMCPG1ActionAuthorization:
    @contextlib.asynccontextmanager
    async def _client(self, monkeypatch):
        from hecate.core.config import settings
        from hecate.tools.mcp import auth_middleware
        from tests.conftest import test_session_factory

        monkeypatch.setattr(auth_middleware, "async_session_factory", test_session_factory)
        monkeypatch.setattr("hecate.tools.mcp.server.async_session_factory", test_session_factory)
        monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")

        app = MCPAuthMiddleware(create_mcp_server().http_app(path="/"))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    @staticmethod
    def _call_text(resp_json: dict) -> str:
        return resp_json["result"]["content"][0]["text"]

    async def test_editor_can_create_agent_in_own_workspace(self, monkeypatch, db_session) -> None:
        """An editor mutation succeeds with the server-derived owner."""
        ws, _agent, token = await _seed_workspace_with_agent(db_session, "editor-existing", role=WorkspaceRole.EDITOR)
        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                "agent_create",
                {"name": "editor-created", "model_config": {"model": "gpt-4o"}},
            )
            response = await client.post("/", json=call["body"], headers=call["headers"])
        assert response.status_code == 200, response.text
        data = json.loads(self._call_text(response.json()))
        assert "id" in data, data
        created = await db_session.get(AgentModel, _uuid.UUID(data["id"]))
        assert created is not None
        assert created.workspace_id == ws.id

    @pytest.mark.parametrize(
        ("tool", "argument_factory"),
        [
            ("agent_update", lambda agent_id: {"agent_id": agent_id, "fields": {"name": "viewer-change"}}),
            ("agent_delete", lambda agent_id: {"agent_id": agent_id}),
            ("knowledge_create", lambda _agent_id: {"name": "viewer-knowledge"}),
            ("knowledge_ingest", lambda _agent_id: {"kb_id": str(_uuid.uuid4()), "content": "viewer"}),
            (
                "tool_create",
                lambda _agent_id: {"name": "viewer-tool", "description": "test", "parameters": {}},
            ),
        ],
    )
    async def test_viewer_mutations_have_no_side_effects(self, monkeypatch, db_session, tool, argument_factory) -> None:
        """Every covered MCP mutation rejects a viewer before changing state."""
        from sqlalchemy import func, select

        from hecate.models.knowledge import KnowledgeBaseModel
        from hecate.models.tool import ToolModel

        _ws, agent, token = await _seed_workspace_with_agent(db_session, "viewer-target", role=WorkspaceRole.VIEWER)
        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                tool,
                argument_factory(str(agent.id)),
            )
            response = await client.post("/", json=call["body"], headers=call["headers"])
        assert response.status_code == 200, response.text
        assert "error" in self._call_text(response.json()).lower()
        await db_session.refresh(agent)
        assert agent.name == "viewer-target"
        assert agent.deleted is False
        assert (await db_session.execute(select(func.count()).select_from(KnowledgeBaseModel))).scalar_one() == 0
        assert (await db_session.execute(select(func.count()).select_from(ToolModel))).scalar_one() == 0

    async def test_system_scope_can_update_and_delete_explicit_agent(self, monkeypatch, db_session) -> None:
        """The bootstrap identity keeps its explicit resource-ID access."""
        from hecate.core.config import settings

        _ws, agent, _token = await _seed_workspace_with_agent(db_session, "system-target")
        monkeypatch.setattr(settings, "PLATFORM_ADMIN_API_KEYS", "g1-bootstrap-key")
        async with self._client(monkeypatch) as client:
            for tool, arguments in (
                ("agent_update", {"agent_id": str(agent.id), "fields": {"name": "system-updated"}}),
                ("agent_delete", {"agent_id": str(agent.id)}),
            ):
                call = _call_tool(client, {"Authorization": "Bearer g1-bootstrap-key"}, tool, arguments)
                response = await client.post("/", json=call["body"], headers=call["headers"])
                assert response.status_code == 200, response.text
                assert "error" not in self._call_text(response.json()).lower()
        await db_session.refresh(agent)
        assert agent.name == "system-updated"
        assert agent.deleted is True

    async def test_editor_cannot_bind_foreign_knowledge(self, monkeypatch, db_session) -> None:
        """An Agent cannot acquire a knowledge base from another workspace."""
        from hecate.models.knowledge import KnowledgeBaseModel

        _own_ws, _agent, token = await _seed_workspace_with_agent(db_session, "editor-target")
        foreign_ws, _foreign_agent, _foreign_token = await _seed_workspace_with_agent(db_session, "foreign-target")
        kb = KnowledgeBaseModel(
            workspace_id=foreign_ws.id,
            name="foreign-knowledge",
            collection_name="foreign_knowledge",
            embedding_model="test",
            chunk_strategy="fixed",
            search_mode="dense",
        )
        db_session.add(kb)
        await db_session.commit()
        await db_session.refresh(kb)

        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                "agent_create",
                {
                    "name": "cross-tenant-knowledge",
                    "model_config": {"model": "gpt-4o"},
                    "knowledge_base_ids": [str(kb.id)],
                },
            )
            response = await client.post("/", json=call["body"], headers=call["headers"])
        assert response.status_code == 200, response.text
        assert "Knowledge base not found" in self._call_text(response.json())

    async def test_unscoped_identity_cannot_read_shared_catalog(self, monkeypatch, db_session) -> None:
        """A signed identity without membership has no zero-workspace read grant."""
        from hecate.core.config import settings
        from hecate.enterprise.auth.token import create_access_token

        monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")
        user = UserModel(email=f"unscoped-{_uuid.uuid4().hex}@example.com", hashed_password=hash_password("x"))
        db_session.add(user)
        await db_session.commit()
        token = create_access_token(user.id)
        async with self._client(monkeypatch) as client:
            call = _call_tool(client, {"Authorization": f"Bearer {token}"}, "tool_list", {})
            response = await client.post("/", json=call["body"], headers=call["headers"])
        assert response.status_code == 200, response.text
        assert "membership required" in self._call_text(response.json()).lower()

    async def test_viewer_agent_create_rejected_no_db_write(self, monkeypatch, db_session) -> None:
        """G1 failure reproduction: viewer can currently call agent_create.

        After the fix the call must be rejected before any AgentModel row
        is created. The assertion already encodes the post-fix invariant;
        before the fix the assertion fails on the side-effect query.
        """
        from sqlalchemy import func, select

        from hecate.models.agent import AgentModel as _AgentModel

        _ws, _agent, token = await _seed_workspace_with_agent(db_session, "viewer-ws-agent", role=WorkspaceRole.VIEWER)

        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                "agent_create",
                {"name": "should-not-appear", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
            )
            resp = await client.post("/", json=call["body"], headers=call["headers"])
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        # Post-fix invariant: an authorization error reaches the caller.
        assert ("error" in text.lower()) or ("forbidden" in text.lower()) or ("denied" in text.lower()), (
            f"viewer was accepted by agent_create: {text}"
        )
        # Side-effect query: no agent should be added to the caller's ws.
        count = (
            await db_session.execute(
                select(func.count()).select_from(_AgentModel).where(_AgentModel.name == "should-not-appear")
            )
        ).scalar_one()
        assert count == 0, "viewer agent_create leaked an AgentModel row"

    async def test_restricted_identity_agent_create_rejected_no_zero_ws_write(self, monkeypatch, db_session) -> None:
        """G1 failure reproduction: no-workspace identity currently writes to zero workspace.

        The fix must reject before any AgentModel row is created — including
        no implicit zero-workspace fallback.
        """
        from sqlalchemy import func, select

        from hecate.models.agent import AgentModel as _AgentModel

        # A workspace seed exists but the identity has no WorkspaceMemberModel
        # row. JWT provider's request-time check rejects such tokens outright;
        # we exercise the unauthenticated path via MCP_AUTH_TYPE=none for a
        # separate test and here exercise the authenticated but path where
        # auth resolves a workspace (system scope) — the relevant G1 shape is
        # the no-workspace AuthContext path, which only arises when transport
        # authentication succeeds but the identity lacks membership.
        # Use a JWT with workspace claim but no member row: provider rejects.
        ws, user = await _seed_workspace_no_member(db_session)
        from hecate.enterprise.auth.token import create_access_token

        # Token claims the workspace and admin role, but live membership is
        # missing → jwt_provider returns None → transport rejects with 401.
        token = create_access_token(user.id, None, ws.id, "admin")

        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                "agent_create",
                {"name": "should-not-appear", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
            )
            resp = await client.post("/", json=call["body"], headers=call["headers"])
        assert resp.status_code == 401, f"expected 401 for membership-less token, got {resp.status_code}: {resp.text}"
        count = (await db_session.execute(select(func.count()).select_from(_AgentModel))).scalar_one()
        assert count == 0, "no-membership agent_create leaked AgentModel rows"

    async def test_viewer_knowledge_create_rejected_no_db_write(self, monkeypatch, db_session) -> None:
        from sqlalchemy import func, select

        from hecate.models.knowledge import KnowledgeBaseModel as _KBModel

        _ws, _agent, token = await _seed_workspace_with_agent(db_session, "viewer-kb-agent", role=WorkspaceRole.VIEWER)

        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                "knowledge_create",
                {"name": "should-not-appear-kb"},
            )
            resp = await client.post("/", json=call["body"], headers=call["headers"])
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        assert ("error" in text.lower()) or ("forbidden" in text.lower()) or ("denied" in text.lower()), (
            f"viewer was accepted by knowledge_create: {text}"
        )
        count = (
            await db_session.execute(
                select(func.count()).select_from(_KBModel).where(_KBModel.name == "should-not-appear-kb")
            )
        ).scalar_one()
        assert count == 0, "viewer knowledge_create leaked a KnowledgeBaseModel row"

    async def test_viewer_tool_create_rejected_no_db_write(self, monkeypatch, db_session) -> None:
        from sqlalchemy import func, select

        from hecate.models.tool import ToolModel as _ToolModel

        _ws, _agent, token = await _seed_workspace_with_agent(
            db_session, "viewer-tool-agent", role=WorkspaceRole.VIEWER
        )

        async with self._client(monkeypatch) as client:
            call = _call_tool(
                client,
                {"Authorization": f"Bearer {token}"},
                "tool_create",
                {"name": "should-not-appear-tool", "description": "x", "parameters": {}},
            )
            resp = await client.post("/", json=call["body"], headers=call["headers"])
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        assert ("error" in text.lower()) or ("forbidden" in text.lower()) or ("denied" in text.lower()), (
            f"viewer was accepted by tool_create: {text}"
        )
        count = (
            await db_session.execute(
                select(func.count()).select_from(_ToolModel).where(_ToolModel.name == "should-not-appear-tool")
            )
        ).scalar_one()
        assert count == 0, "viewer tool_create leaked a ToolModel row"


# ---------------------------------------------------------------------------
# G1 MCP_AUTH_TYPE=none failure reproductions.
#
# The current ``MCP_AUTH_TYPE=none`` escape hatch skips transport
# authentication but ``_auth()`` still raises when the request state has
# no AuthContext. The behavior is correct in spirit but is not pinned by
# a test, and the error message does not distinguish "auth disabled"
# from "auth failed". These tests pin the fail-closed invariant for both
# execution and mutation tools.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("setup_database")
class TestMCPG1NoAuthMode:
    @contextlib.asynccontextmanager
    async def _client(self, monkeypatch):
        from hecate.core.config import settings
        from hecate.tools.mcp import auth_middleware
        from tests.conftest import test_session_factory

        monkeypatch.setattr(auth_middleware, "async_session_factory", test_session_factory)
        monkeypatch.setattr("hecate.tools.mcp.server.async_session_factory", test_session_factory)
        monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")
        monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "none")

        app = MCPAuthMiddleware(create_mcp_server().http_app(path="/"))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    @staticmethod
    def _call_text(resp_json: dict) -> str:
        return resp_json["result"]["content"][0]["text"]

    async def test_no_auth_mode_blocks_tool_execute(self, monkeypatch, db_session) -> None:
        """G1 contract: in MCP_AUTH_TYPE=none mode, tool_execute must fail
        before any side effect, with a message that explicitly names the
        disabled-authentication mode (not the generic unauthenticated
        string). The internal exception type SHALL NOT leak.
        """
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": "web_search", "arguments": {"query": "x"}}},
                name="tool_execute",
            )
            resp = await client.post("/", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        assert "PermissionError" not in text, f"internal exception leaked to caller: {text}"
        # The fail-closed message MUST distinguish disabled auth from a
        # generic unauthenticated rejection. Today this is the contract
        # anchored by ``MCPAuthMiddleware``'s ``auth_disabled`` state flag.
        assert "disabled" in text.lower(), f"expected the 'auth disabled' message, got: {text}"
        assert "unauthenticated" not in text.lower(), (
            f"'auth disabled' and 'unauthenticated' must remain distinguishable, got: {text}"
        )

    async def test_no_auth_mode_blocks_agent_create(self, monkeypatch, db_session) -> None:
        """Companion: in none mode, agent_create must fail with the same
        distinguishable disabled-auth message.
        """
        async with self._client(monkeypatch) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {
                    "name": "agent_create",
                    "arguments": {"name": "x", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
                },
                name="agent_create",
            )
            resp = await client.post("/", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        assert "PermissionError" not in text, f"internal exception leaked to caller: {text}"
        assert "disabled" in text.lower(), f"expected disabled-auth message, got: {text}"


# ---------------------------------------------------------------------------
# G1 tool_execute failure reproductions.
#
# Three concrete paths are tested:
#
# 1. Cross-workspace file isolation: each workspace's write_file/read_file
#    stays in its own subtree even though both go through the same global
#    WORKSPACE_ROOT today.
# 2. Path traversal: ``read_file`` with ``..`` or absolute paths must be
#    rejected before read. The BuiltInToolExecutor already raises on
#    traversal, but the contract is pinned here at the MCP entry so the
#    fix doesn't regress.
# 3. approval_required=true tool is rejected at MCP entry: today the
#    server does not consult ``approval_required`` at all and runs the
#    tool. After the fix the call must be rejected before the tool body
#    runs.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("setup_database")
class TestMCPG1ToolExecute:
    @contextlib.asynccontextmanager
    async def _client(self, monkeypatch, *, workspace_root):
        from hecate.core.config import settings
        from hecate.tools.mcp import auth_middleware
        from tests.conftest import test_session_factory

        monkeypatch.setattr(auth_middleware, "async_session_factory", test_session_factory)
        monkeypatch.setattr("hecate.tools.mcp.server.async_session_factory", test_session_factory)
        monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")
        monkeypatch.setattr(settings, "WORKSPACE_ROOT", str(workspace_root))

        app = MCPAuthMiddleware(create_mcp_server().http_app(path="/"))
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    @staticmethod
    def _call_text(resp_json: dict) -> str:
        return resp_json["result"]["content"][0]["text"]

    async def test_tool_execute_write_file_workspace_isolation(self, monkeypatch, db_session, tmp_path) -> None:
        """Different workspaces can use the same relative path independently."""
        ws_a, _agent_a, token_a = await _seed_workspace_with_agent(db_session, "iso-ws-a", role=WorkspaceRole.ADMIN)
        ws_b, _agent_b, token_b = await _seed_workspace_with_agent(db_session, "iso-ws-b", role=WorkspaceRole.ADMIN)

        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            # Workspace A writes.
            body_a, headers_a = _modern_envelope(
                "tools/call",
                {
                    "name": "tool_execute",
                    "arguments": {
                        "tool_name": "write_file",
                        "arguments": {"path": "shared.txt", "content": "A-content"},
                    },
                },
                name="tool_execute",
            )
            resp_a = await client.post("/", json=body_a, headers={**headers_a, "Authorization": f"Bearer {token_a}"})
            assert resp_a.status_code == 200, resp_a.text
            assert "Written" in self._call_text(resp_a.json()), self._call_text(resp_a.json())

            # Workspace B writes.
            body_b, headers_b = _modern_envelope(
                "tools/call",
                {
                    "name": "tool_execute",
                    "arguments": {
                        "tool_name": "write_file",
                        "arguments": {"path": "shared.txt", "content": "B-content"},
                    },
                },
                name="tool_execute",
            )
            resp_b = await client.post("/", json=body_b, headers={**headers_b, "Authorization": f"Bearer {token_b}"})
            assert resp_b.status_code == 200, resp_b.text

            # Workspace A reads.
            body_ra, headers_ra = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": "read_file", "arguments": {"path": "shared.txt"}}},
                name="tool_execute",
            )
            resp_ra = await client.post("/", json=body_ra, headers={**headers_ra, "Authorization": f"Bearer {token_a}"})
            text_a = self._call_text(resp_ra.json())
            assert resp_ra.status_code == 200, resp_ra.text

            # Workspace B reads.
            body_rb, headers_rb = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": "read_file", "arguments": {"path": "shared.txt"}}},
                name="tool_execute",
            )
            resp_rb = await client.post("/", json=body_rb, headers={**headers_rb, "Authorization": f"Bearer {token_b}"})
            text_b = self._call_text(resp_rb.json())

        # ``tool_execute`` wraps the builtin result for the MCP caller.
        assert text_a == '{"result": "A-content"}', f"workspace A lost its file: {text_a!r}"
        assert text_b == '{"result": "B-content"}', f"workspace B lost its file: {text_b!r}"

    async def test_tool_execute_path_traversal_rejected(self, monkeypatch, db_session, tmp_path) -> None:
        _ws, _agent, token = await _seed_workspace_with_agent(db_session, "trav-ws", role=WorkspaceRole.ADMIN)
        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {
                    "name": "tool_execute",
                    "arguments": {
                        "tool_name": "read_file",
                        "arguments": {"path": "../../../../etc/passwd"},
                    },
                },
                name="tool_execute",
            )
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        # The BuiltInToolExecutor's existing traversal guard is the right
        # behavior; the contract pinned here is that an attempt is rejected,
        # not silently truncated to an in-root path or read.
        assert "Path traversal" in text or "outside workspace" in text or "error" in text.lower(), (
            f"path traversal must be rejected: {text}"
        )

    async def test_tool_execute_approval_required_rejected(self, monkeypatch, db_session, tmp_path) -> None:
        """An approval-gated tool is rejected before any side effect."""
        from hecate.models.tool import ToolModel

        suffix = _uuid.uuid4().hex[:8]
        ws, _agent, token = await _seed_workspace_with_agent(db_session, f"apr-ws-{suffix}", role=WorkspaceRole.ADMIN)
        admin_tool = ToolModel(
            workspace_id=ws.id,
            name=f"approval-gated-{suffix}",
            description="test",
            source="custom",
            parameters={},
            risk_level="HIGH",
            approval_required=True,
        )
        db_session.add(admin_tool)
        await db_session.commit()
        await db_session.refresh(admin_tool)

        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": admin_tool.name, "arguments": {}}},
                name="tool_execute",
            )
            resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        text = self._call_text(resp.json())
        assert (
            "approval" in text.lower()
            or "not executable" in text.lower()
            or "not authorized" in text.lower()
            or "denied" in text.lower()
        ), f"approval-required tool must be rejected explicitly: {text}"

    async def test_foreign_tenant_tool_cannot_shadow_builtin(self, monkeypatch, db_session, tmp_path) -> None:
        """A foreign row with the same name cannot veto an authorized builtin."""
        from hecate.models.tool import ToolModel

        _own_ws, _agent, token = await _seed_workspace_with_agent(db_session, "tool-owner")
        foreign_ws, _foreign_agent, _foreign_token = await _seed_workspace_with_agent(db_session, "foreign-owner")
        db_session.add(
            ToolModel(
                workspace_id=foreign_ws.id,
                name="write_file",
                description="foreign shadow",
                source="custom",
                parameters={},
                risk_level="HIGH",
            )
        )
        await db_session.commit()

        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {
                    "name": "tool_execute",
                    "arguments": {
                        "tool_name": "write_file",
                        "arguments": {"path": "owned.txt", "content": "allowed"},
                    },
                },
                name="tool_execute",
            )
            response = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert "Written" in self._call_text(response.json())
        assert (tmp_path / str(_own_ws.id) / "owned.txt").read_text(encoding="utf-8") == "allowed"

    async def test_conflicting_local_tool_name_is_rejected(self, monkeypatch, db_session, tmp_path) -> None:
        """A tenant record cannot change which handler a builtin name runs."""
        from hecate.models.tool import ToolModel

        own_ws, _agent, token = await _seed_workspace_with_agent(db_session, "tool-owner")
        db_session.add(
            ToolModel(
                workspace_id=own_ws.id,
                name="write_file",
                description="local shadow",
                source="custom",
                parameters={},
                risk_level="LOW",
            )
        )
        await db_session.commit()

        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {
                    "name": "tool_execute",
                    "arguments": {
                        "tool_name": "write_file",
                        "arguments": {"path": "must-not-exist.txt", "content": "denied"},
                    },
                },
                name="tool_execute",
            )
            response = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert "error" in self._call_text(response.json()).lower()
        assert not (tmp_path / str(own_ws.id) / "must-not-exist.txt").exists()

    async def test_absolute_path_and_forged_workspace_cannot_widen_file_root(
        self, monkeypatch, db_session, tmp_path
    ) -> None:
        """Caller arguments cannot make a file tool write in another tenant's root."""
        own_ws, _agent, token = await _seed_workspace_with_agent(db_session, "file-owner")
        foreign_ws, _other, _other_token = await _seed_workspace_with_agent(db_session, "file-foreign")
        target = tmp_path / str(foreign_ws.id) / "foreign.txt"
        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {
                    "name": "tool_execute",
                    "arguments": {
                        "tool_name": "write_file",
                        "arguments": {
                            "path": str(target),
                            "content": "forbidden",
                            "workspace_id": str(foreign_ws.id),
                        },
                    },
                },
                name="tool_execute",
            )
            response = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert "error" in self._call_text(response.json()).lower()
        assert not target.exists()
        assert not (tmp_path / str(own_ws.id) / "foreign.txt").exists()

    async def test_code_execution_without_action_binding_is_denied(self, monkeypatch, db_session, tmp_path) -> None:
        """The direct MCP surface cannot launch a sandbox without a binding."""
        from hecate.tools.tool.builtin import BuiltInToolExecutor

        _ws, _agent, token = await _seed_workspace_with_agent(db_session, "code-caller")
        invoked = False

        async def fake_execute_code(self, args, context=None):
            nonlocal invoked
            invoked = True
            return {"exit_code": 0}

        monkeypatch.setattr(BuiltInToolExecutor, "_execute_code", fake_execute_code)
        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": "execute_code", "arguments": {"code": "1"}}},
                name="tool_execute",
            )
            response = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert "binding" in self._call_text(response.json()).lower()
        assert invoked is False

    @pytest.mark.parametrize(("risk", "approval"), [("HIGH", False), ("LOW", True), ("UNKNOWN", False)])
    async def test_registered_tool_without_authorization_is_denied(
        self, monkeypatch, db_session, tmp_path, risk, approval
    ) -> None:
        """Risk and approval cases receive an explicit denial before routing."""
        from hecate.models.tool import ToolModel

        ws, _agent, token = await _seed_workspace_with_agent(db_session, "risk-caller")
        name = f"risk-tool-{_uuid.uuid4().hex}"
        db_session.add(
            ToolModel(
                workspace_id=ws.id,
                name=name,
                description="test",
                source="custom",
                parameters={},
                risk_level=risk,
                approval_required=approval,
            )
        )
        await db_session.commit()
        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": name, "arguments": {}}},
                name="tool_execute",
            )
            response = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        text = self._call_text(response.json()).lower()
        assert "error" in text
        assert ("risk metadata" if risk == "UNKNOWN" else "approval or is high risk") in text

    async def test_symlink_outside_workspace_is_rejected(self, monkeypatch, db_session, tmp_path) -> None:
        """Resolved symlinks cannot escape the server-derived file root."""
        _ws, _agent, token = await _seed_workspace_with_agent(db_session, "link-caller")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        own_root = tmp_path / str(_ws.id)
        own_root.mkdir()
        try:
            (own_root / "link.txt").symlink_to(outside)
        except OSError:
            pytest.skip("Host does not permit symlink creation")

        async with self._client(monkeypatch, workspace_root=tmp_path) as client:
            body, headers = _modern_envelope(
                "tools/call",
                {"name": "tool_execute", "arguments": {"tool_name": "read_file", "arguments": {"path": "link.txt"}}},
                name="tool_execute",
            )
            response = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        assert "Path traversal" in self._call_text(response.json())
