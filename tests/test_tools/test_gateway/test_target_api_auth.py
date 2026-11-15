"""Authorization regressions for the gateway target management API.

Real routes on ``hecate.main.app`` with the repository's identity fixtures:
anonymous requests exercise the real 401 chain, workspace members run under
the shared ``client`` identity overrides, and platform admins pass the
deploy-time token gate. Covers the disabled-gateway 404 surface, the
401/403/404 semantics per role, the identity-derived workspace scope (a
request body cannot assign another tenant), credential redaction, the
platform-admin boundary for global and ``mcp`` targets, and zero writes on
every rejected request.
"""

from __future__ import annotations

import uuid
from hashlib import sha256

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.deps_workspace import WorkspaceRole, get_auth_context
from hecate.enterprise.auth.token import create_access_token
from hecate.main import app
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.gateway_target import GatewayTargetModel
from hecate.models.tool import ToolModel
from hecate.models.user import UserModel
from hecate.tools.api.mcp import get_mcp_manager

_REST_PAYLOAD = {
    "name": "crm",
    "kind": "rest",
    "base_url": "https://api.example.com",
    "spec": {"openapi": "3.0.0", "paths": {"/ping": {"get": {"operationId": "ping"}}}},
    "credentials": {"headers": {"Authorization": "Bearer top-secret"}},
}


@pytest.fixture(autouse=True)
def _gateway_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "GATEWAY_ENABLED", True)


@pytest.fixture
def _restore_overrides():
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _member_ctx(default_workspace, role: WorkspaceRole, *, workspace_id: uuid.UUID | None = None) -> AuthContext:
    """A JWT workspace identity with the given role in (another) workspace."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=default_workspace.org_id,
        workspace_id=workspace_id or default_workspace.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


def _swap_identity(ctx: AuthContext) -> None:
    async def override() -> AuthContext:
        return ctx

    app.dependency_overrides[get_auth_context] = override


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _counts(db_session) -> tuple[int, int]:
    targets = (await db_session.execute(select(func.count()).select_from(GatewayTargetModel))).scalar_one()
    tools = (await db_session.execute(select(func.count()).select_from(ToolModel))).scalar_one()
    return targets, tools


async def test_disabled_gateway_returns_404_to_every_caller(
    client: AsyncClient, anonymous_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enabled gate runs before authentication: a disabled gateway is a
    404 route for anonymous and authenticated callers alike."""
    monkeypatch.setattr(settings, "GATEWAY_ENABLED", False)
    for cl in (client, anonymous_client):
        for method, url, payload in (
            ("GET", "/api/gateway/targets", None),
            ("POST", "/api/gateway/targets", dict(_REST_PAYLOAD)),
            ("GET", f"/api/gateway/targets/{uuid.uuid4()}", None),
            ("PATCH", f"/api/gateway/targets/{uuid.uuid4()}", {"is_active": False}),
            ("DELETE", f"/api/gateway/targets/{uuid.uuid4()}", None),
        ):
            response = await cl.request(method, url, json=payload)
            assert response.status_code == 404, f"{method} {url} -> {response.status_code}"


async def test_anonymous_requests_are_unauthorized(anonymous_client: AsyncClient) -> None:
    for method, url, payload in (
        ("GET", "/api/gateway/targets", None),
        ("POST", "/api/gateway/targets", dict(_REST_PAYLOAD)),
        ("GET", f"/api/gateway/targets/{uuid.uuid4()}", None),
        ("PATCH", f"/api/gateway/targets/{uuid.uuid4()}", {"is_active": False}),
        ("DELETE", f"/api/gateway/targets/{uuid.uuid4()}", None),
    ):
        response = await anonymous_client.request(method, url, json=payload)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"
        assert response.json()["detail"]["error"]["code"] == "UNAUTHORIZED"


async def test_workspace_admin_registers_rest_target_in_own_workspace(
    client: AsyncClient, db_session, default_workspace
) -> None:
    created = await client.post("/api/gateway/targets", json=_REST_PAYLOAD)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["target"]["workspace_id"] == str(default_workspace.id)
    assert body["import"]["imported"] == ["crm__ping"]
    # Credentials redacted everywhere.
    assert body["target"]["credentials"] == {"headers": {"Authorization": "***"}}
    assert "top-secret" not in created.text
    targets, tools = await _counts(db_session)
    assert (targets, tools) == (1, 1)

    # A valid re-projection replaces the active tools in the authorized workspace.
    target_url = f"/api/gateway/targets/{body['target']['id']}"
    patched = await client.patch(
        target_url, json={"spec": {"openapi": "3.0.0", "paths": {"/status": {"get": {"operationId": "status"}}}}}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["import"]["imported"] == ["crm__status"]
    active = (await db_session.execute(select(ToolModel).where(ToolModel.deleted.is_(False)))).scalars().all()
    assert [(tool.workspace_id, tool.name) for tool in active] == [(default_workspace.id, "crm__status")]


async def test_foreign_workspace_id_in_body_is_refused(client: AsyncClient, db_session) -> None:
    """A request body cannot assign another tenant as the owner."""
    before = await _counts(db_session)
    servers_before = get_mcp_manager().list_servers()
    payload = {**_REST_PAYLOAD, "workspace_id": str(uuid.uuid4())}
    response = await client.post("/api/gateway/targets", json=payload)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert await _counts(db_session) == before
    assert get_mcp_manager().list_servers() == servers_before


async def test_global_target_creation_is_platform_admin_territory(
    client: AsyncClient, db_session, platform_admin_token
) -> None:
    """Explicit null selects global scope and requires platform authority."""
    before = await _counts(db_session)
    denied = await client.post("/api/gateway/targets", json={**_REST_PAYLOAD, "workspace_id": None})
    assert denied.status_code == 403
    assert await _counts(db_session) == before

    headers = {"Authorization": f"Bearer {platform_admin_token}"}
    created = await client.post("/api/gateway/targets", json={**_REST_PAYLOAD, "workspace_id": None}, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["target"]["workspace_id"] is None
    targets, _ = await _counts(db_session)
    assert targets == before[0] + 1


async def test_viewer_reads_own_workspace_editor_writes_are_forbidden(
    client: AsyncClient, db_session, default_workspace, _restore_overrides
) -> None:
    """VIEWER reads own-workspace targets; EDITOR mutations are 403 and
    write nothing."""
    created = await client.post(
        "/api/gateway/targets", json={**_REST_PAYLOAD, "workspace_id": str(default_workspace.id)}
    )
    assert created.status_code == 201
    target_id = created.json()["target"]["id"]

    _swap_identity(_member_ctx(default_workspace, WorkspaceRole.VIEWER))
    assert (await client.get("/api/gateway/targets")).status_code == 200
    got = await client.get(f"/api/gateway/targets/{target_id}")
    assert got.status_code == 200 and got.json()["credentials"] == {"headers": {"Authorization": "***"}}

    _swap_identity(_member_ctx(default_workspace, WorkspaceRole.EDITOR))
    before = await _counts(db_session)
    servers_before = get_mcp_manager().list_servers()
    for method, url, payload in (
        ("POST", "/api/gateway/targets", {**_REST_PAYLOAD, "workspace_id": str(default_workspace.id)}),
        ("PATCH", f"/api/gateway/targets/{target_id}", {"is_active": False}),
        ("DELETE", f"/api/gateway/targets/{target_id}", None),
    ):
        response = await client.request(method, url, json=payload)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert await _counts(db_session) == before
    assert get_mcp_manager().list_servers() == servers_before

    # The viewer read never mutated the target.
    _swap_identity(_member_ctx(default_workspace, WorkspaceRole.ADMIN))
    still_there = await client.get(f"/api/gateway/targets/{target_id}")
    assert still_there.status_code == 200 and still_there.json()["is_active"] is True


async def test_cross_workspace_and_missing_targets_are_one_404(
    client: AsyncClient, db_session, default_workspace, _restore_overrides
) -> None:
    """A caller outside the owning workspace cannot distinguish foreign
    targets from missing ones, on every read and write."""
    created = await client.post(
        "/api/gateway/targets",
        json={**_REST_PAYLOAD, "name": "foreign", "workspace_id": str(default_workspace.id)},
    )
    assert created.status_code == 201
    foreign_id = created.json()["target"]["id"]

    # A second workspace the caller does not belong to.
    _swap_identity(_member_ctx(default_workspace, WorkspaceRole.ADMIN, workspace_id=uuid.uuid4()))
    for method, url, payload in (
        ("GET", f"/api/gateway/targets/{foreign_id}", None),
        ("PATCH", f"/api/gateway/targets/{foreign_id}", {"is_active": False}),
        ("DELETE", f"/api/gateway/targets/{foreign_id}", None),
        ("GET", f"/api/gateway/targets/{uuid.uuid4()}", None),
        ("PATCH", f"/api/gateway/targets/{uuid.uuid4()}", {"is_active": False}),
        ("DELETE", f"/api/gateway/targets/{uuid.uuid4()}", None),
    ):
        response = await client.request(method, url, json=payload)
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
    listing = await client.get("/api/gateway/targets")
    assert listing.status_code == 200 and listing.json() == []
    # The rejected writes left exactly the own-workspace target row and its
    # projected tool.
    assert await _counts(db_session) == (1, 1)


async def test_global_targets_are_platform_admin_managed(
    anonymous_client: AsyncClient,
    db_session,
    platform_admin_token,
    platform_admin_email,
    default_workspace,
    test_user_id,
    workspace_member_fixture,
    monkeypatch,
) -> None:
    """Bootstrap keys and allowlisted JWTs can manage globals; members cannot probe them."""
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_API_KEYS", platform_admin_token)
    db_session.add(UserModel(id=test_user_id, email="member@example.com", hashed_password="unused"))
    await db_session.commit()
    member = _bearer(create_access_token(test_user_id, default_workspace.org_id, default_workspace.id, "admin"))
    client = anonymous_client
    headers = _bearer(platform_admin_token)

    created = await client.post(
        "/api/gateway/targets",
        json={
            **_REST_PAYLOAD,
            "name": "global-crm",
            "credentials": {"headers": {"Authorization": "Bearer g-secret"}},
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    global_id = created.json()["target"]["id"]
    assert created.json()["target"]["workspace_id"] is None
    assert "g-secret" not in created.text

    resource = f"/api/gateway/targets/{global_id}"
    assert (await client.get(resource, headers=member)).status_code == 404
    assert (await client.patch(resource, headers=member, json={"is_active": False})).status_code == 404
    assert (await client.delete(resource, headers=member)).status_code == 404
    assert (await client.get("/api/gateway/targets", headers=member)).json() == []
    admin_list = await client.get("/api/gateway/targets", headers=headers)
    assert [t["name"] for t in admin_list.json()] == ["global-crm"]

    admin = UserModel(email=platform_admin_email, hashed_password="unused")
    db_session.add(admin)
    await db_session.commit()
    jwt = _bearer(create_access_token(admin.id))
    assert [t["id"] for t in (await client.get("/api/gateway/targets", headers=jwt)).json()] == [global_id]
    got = await client.get(resource, headers=jwt)
    assert got.status_code == 200 and got.json()["credentials"] == {"headers": {"Authorization": "***"}}
    patched = await client.patch(resource, json={"is_active": False}, headers=jwt)
    assert patched.status_code == 200 and patched.json()["target"]["is_active"] is False
    # The global target carried a spec, so its projected tool row exists.
    assert await _counts(db_session) == (1, 1)


async def test_db_issued_system_scope_is_not_platform_admin(
    anonymous_client: AsyncClient, db_session, test_user_id
) -> None:
    """The real authentication chain rejects database system keys."""
    token = "hcat_gateway_db_system_test"
    db_session.add(
        ApiKeyModel(
            name="legacy-system",
            key_hash=sha256(token.encode()).hexdigest(),
            key_prefix=token[:12],
            scope=ApiKeyScope.SYSTEM,
            created_by=test_user_id,
        )
    )
    await db_session.commit()
    before = await _counts(db_session)
    servers_before = get_mcp_manager().list_servers()
    for method, url, payload in (
        ("POST", "/api/gateway/targets", {**_REST_PAYLOAD, "workspace_id": str(uuid.uuid4())}),
        ("POST", "/api/gateway/targets", dict(_REST_PAYLOAD)),
        ("POST", "/api/gateway/targets", {"name": "fed", "kind": "mcp", "base_url": "https://mcp.example.com"}),
    ):
        response = await anonymous_client.request(method, url, json=payload, headers=_bearer(token))
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"
    assert await _counts(db_session) == before
    assert get_mcp_manager().list_servers() == servers_before


async def test_legacy_env_key_cannot_read_any_management_targets(
    anonymous_client: AsyncClient, platform_admin_token, monkeypatch
) -> None:
    """System scope by itself grants no access to target management."""
    legacy_key = "gateway-legacy-env-test"
    monkeypatch.setattr(settings, "HECATE_API_KEYS", legacy_key)
    created = await anonymous_client.post(
        "/api/gateway/targets", json=_REST_PAYLOAD, headers=_bearer(platform_admin_token)
    )
    assert created.status_code == 201, created.text
    target_id = created.json()["target"]["id"]
    for url in ("/api/gateway/targets", f"/api/gateway/targets/{target_id}"):
        response = await anonymous_client.get(url, headers=_bearer(legacy_key))
        assert response.status_code == 403


async def test_workspace_key_uses_live_membership_and_never_the_platform_email_allowlist(
    anonymous_client: AsyncClient,
    db_session,
    default_workspace,
    workspace_member_fixture,
    test_user_id,
    platform_admin_email,
) -> None:
    """A database key inherits neither a synthetic admin role nor the owner's JWT authority."""
    token = "hcat_gateway_workspace_test"
    db_session.add(UserModel(id=test_user_id, email=platform_admin_email, hashed_password="unused"))
    db_session.add(
        ApiKeyModel(
            name="workspace",
            key_hash=sha256(token.encode()).hexdigest(),
            key_prefix=token[:12],
            scope=ApiKeyScope.WORKSPACE,
            org_id=default_workspace.org_id,
            workspace_id=default_workspace.id,
            created_by=test_user_id,
        )
    )
    await db_session.commit()
    headers = _bearer(token)
    url = "/api/gateway/targets"
    denied_global = await anonymous_client.post(url, json={**_REST_PAYLOAD, "workspace_id": None}, headers=headers)
    assert denied_global.status_code == 403
    assert await _counts(db_session) == (0, 0)

    created = await anonymous_client.post(url, json=_REST_PAYLOAD, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["target"]["workspace_id"] == str(default_workspace.id)
    detail = f"{url}/{created.json()['target']['id']}"

    workspace_member_fixture.role = WorkspaceRole.VIEWER
    await db_session.commit()
    assert (await anonymous_client.get(detail, headers=headers)).status_code == 200
    assert (
        await anonymous_client.post(url, json={**_REST_PAYLOAD, "name": "denied"}, headers=headers)
    ).status_code == 403
    assert (await anonymous_client.patch(detail, json={"is_active": False}, headers=headers)).status_code == 403
    assert (await anonymous_client.delete(detail, headers=headers)).status_code == 403
    assert await _counts(db_session) == (1, 1)

    workspace_member_fixture.deleted = True
    await db_session.commit()
    assert (await anonymous_client.get(url, headers=headers)).status_code == 403
    assert (await anonymous_client.get(detail, headers=headers)).status_code == 403


async def test_workspace_admin_cannot_manage_own_mcp_target(
    client: AsyncClient, db_session, platform_admin_token, default_workspace
) -> None:
    """``mcp`` management is platform-admin gated even for the owning
    workspace, until the shared registry indexes by workspace."""
    headers = {"Authorization": f"Bearer {platform_admin_token}"}
    created = await client.post(
        "/api/gateway/targets",
        json={
            "name": "ws-fed",
            "kind": "mcp",
            "base_url": "https://mcp.example.com",
            "workspace_id": str(default_workspace.id),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    target_id = created.json()["target"]["id"]

    servers_before = get_mcp_manager().list_servers()
    for method, url, payload in (
        ("PATCH", f"/api/gateway/targets/{target_id}", {"is_active": False}),
        ("DELETE", f"/api/gateway/targets/{target_id}", None),
    ):
        response = await client.request(method, url, json=payload)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert get_mcp_manager().list_servers() == servers_before
    assert await _counts(db_session) == (1, 0)

    deactivated = await client.delete(f"/api/gateway/targets/{target_id}", headers=headers)
    assert deactivated.status_code == 200
