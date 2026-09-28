"""S06 — MCP/REST permission negatives produce the same authorization result.

Manifest: S06 (P06). Gated on g1-mcp-action-enforcement (merged as #185).
Every negative is decided from the server-side trusted context, never from
client-asserted fields:

- viewer role: agent creation rejected at the MCP entry AND at the REST
  entry — same role, same action, same denial (parity);
- cross-tenant: a workspace's token cannot execute another workspace's tool;
- unapproved write: an approval_required tool is rejected before execution.

The MCP server runs behind its real ``MCPAuthMiddleware`` (JWT verified
against the test session factory); the REST side injects a VIEWER
``AuthContext`` through the real dependency so the role decision is the only
variable.
"""

from __future__ import annotations

import contextlib
import uuid as _uuid
from collections.abc import AsyncGenerator

import httpx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from hecate.enterprise.auth.password import hash_password
from hecate.enterprise.auth.token import create_access_token
from hecate.models.agent import AgentModel
from hecate.models.organization import OrganizationModel
from hecate.models.tool import ToolModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


async def _seed_workspace(db_session, name: str, role: WorkspaceRole):
    """Create user/org/workspace/member(role); return (ws, access_token)."""
    suffix = _uuid.uuid4().hex[:8]
    user = UserModel(email=f"s06-{suffix}@example.com", hashed_password=hash_password("securepass123"))
    db_session.add(user)
    await db_session.flush()
    org = OrganizationModel(name=f"Org {suffix}", slug=f"org-{suffix}", owner_id=user.id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name=f"WS {suffix}", slug=f"ws-{suffix}")
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMemberModel(user_id=user.id, workspace_id=ws.id, role=role))
    await db_session.commit()
    return ws, create_access_token(user.id, org.id, ws.id, role.value)


def _modern_envelope(method: str, params: dict | None = None, *, name: str | None = None) -> tuple[dict, dict]:
    """Build a 2026-07-28 JSON-RPC envelope body and standard headers."""
    body: dict = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "scenario", "version": "0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    if params:
        body["params"].update(params)
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if name:
        headers["Mcp-Name"] = name
    return body, headers


def _call_text(resp_json: dict) -> str:
    return resp_json["result"]["content"][0]["text"]


@contextlib.asynccontextmanager
async def _mcp_client(monkeypatch) -> AsyncGenerator[httpx.AsyncClient, None]:
    """MCP server behind its real auth middleware, on the test session factory."""
    from hecate.core.config import settings
    from hecate.tools.mcp import auth_middleware
    from hecate.tools.mcp.auth_middleware import MCPAuthMiddleware
    from hecate.tools.mcp.server import create_mcp_server
    from tests.conftest import test_session_factory

    monkeypatch.setattr(auth_middleware, "async_session_factory", test_session_factory)
    monkeypatch.setattr("hecate.tools.mcp.server.async_session_factory", test_session_factory)
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")

    app = MCPAuthMiddleware(create_mcp_server().http_app(path="/"))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@contextlib.asynccontextmanager
async def _rest_viewer_client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """REST client whose injected AuthContext reports VIEWER for its own workspace."""
    from hecate.core.auth_context import AuthContext
    from hecate.core.database import get_db
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app
    from tests.conftest import test_session_factory

    org = OrganizationModel(
        name=f"S06 org {_uuid.uuid4().hex[:8]}", slug=f"s06-org-{_uuid.uuid4().hex[:8]}", owner_id=_uuid.uuid4()
    )
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name="S06 viewer ws", slug=f"s06-viewer-{_uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    await db_session.commit()

    ctx = AuthContext(
        user_id=_uuid.uuid4(),
        org_id=None,
        workspace_id=ws.id,
        role=WorkspaceRole.VIEWER,
        auth_method="jwt",
        api_key_scope=None,
    )

    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_auth_context():
        return ctx

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = override_get_auth_context
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_context, None)


AGENT_CREATE_ARGS = {"name": "scenario-agent", "model_config": {"model": "gpt-4o"}, "mode": "chat"}


async def test_s06_viewer_agent_create_rejected_on_both_entries(db_session, monkeypatch, scenario_event_store) -> None:
    """Same role + same action → same denial on MCP and REST (no DB write)."""
    from sqlalchemy import func, select

    from tests.conftest import test_session_factory

    _ws, viewer_token = await _seed_workspace(db_session, "viewer-ws", role=WorkspaceRole.VIEWER)

    async with _mcp_client(monkeypatch) as client:
        body, headers = _modern_envelope(
            "tools/call", {"name": "agent_create", "arguments": AGENT_CREATE_ARGS}, name="agent_create"
        )
        resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 200, resp.text
    mcp_text = _call_text(resp.json())
    assert ("denied" in mcp_text.lower()) or ("forbidden" in mcp_text.lower()) or ("error" in mcp_text.lower()), (
        f"MCP entry must deny the viewer: {mcp_text}"
    )

    async with _rest_viewer_client(db_session) as rest:
        rest_resp = await rest.post("/api/agents", json=AGENT_CREATE_ARGS)
    assert rest_resp.status_code == 403, rest_resp.text

    async with test_session_factory() as db:
        count = (await db.execute(select(func.count()).select_from(AgentModel))).scalar_one()
    assert count == 0, "neither entry may leak an AgentModel row for a viewer"


async def test_s06_cross_tenant_tool_execute_rejected(db_session, monkeypatch) -> None:
    """A workspace's token cannot execute another workspace's tool."""
    own_ws, own_token = await _seed_workspace(db_session, "own-ws", role=WorkspaceRole.ADMIN)
    foreign_ws, _foreign_token = await _seed_workspace(db_session, "foreign-ws", role=WorkspaceRole.ADMIN)

    foreign_tool = ToolModel(
        workspace_id=foreign_ws.id,
        name=f"foreign-ticket-tool-{_uuid.uuid4().hex[:6]}",
        description="other tenant's protected write",
        source="custom",
        parameters={},
        risk_level="HIGH",
    )
    db_session.add(foreign_tool)
    await db_session.commit()

    async with _mcp_client(monkeypatch) as client:
        body, headers = _modern_envelope(
            "tools/call",
            {"name": "tool_execute", "arguments": {"tool_name": foreign_tool.name, "arguments": {}}},
            name="tool_execute",
        )
        resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {own_token}"})
    assert resp.status_code == 200, resp.text
    text = _call_text(resp.json())
    assert ("not found" in text.lower()) or ("error" in text.lower()) or ("denied" in text.lower()), (
        f"cross-tenant tool must be invisible to another workspace's token: {text}"
    )
    assert own_ws.id != foreign_ws.id


async def test_s06_unapproved_write_rejected_before_execution(db_session, monkeypatch) -> None:
    """An approval_required tool is rejected at the entry before any side effect."""
    ws, token = await _seed_workspace(db_session, "approval-ws", role=WorkspaceRole.ADMIN)
    gated_tool = ToolModel(
        workspace_id=ws.id,
        name=f"gated-ticket-{_uuid.uuid4().hex[:6]}",
        description="approval-gated protected write",
        source="custom",
        parameters={},
        risk_level="HIGH",
        approval_required=True,
    )
    db_session.add(gated_tool)
    await db_session.commit()

    async with _mcp_client(monkeypatch) as client:
        body, headers = _modern_envelope(
            "tools/call",
            {"name": "tool_execute", "arguments": {"tool_name": gated_tool.name, "arguments": {}}},
            name="tool_execute",
        )
        resp = await client.post("/", json=body, headers={**headers, "Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    text = _call_text(resp.json())
    assert (
        ("approval" in text.lower())
        or ("not authorized" in text.lower())
        or ("denied" in text.lower())
        or ("not executable" in text.lower())
    ), f"approval-gated tool must be rejected explicitly: {text}"
