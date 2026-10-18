"""Agent version lifecycle access boundary (step11).

Covers the two-level boundary enforced by ``AgentVersionService``:

- Workspace ownership: every one of the 13 lifecycle endpoints surfaces a
  cross-workspace agent as not found — reads disclose nothing, writes
  change nothing (these double as the per-endpoint "the API always passes
  the request identity" guards: dropping the pass would turn these 404s
  into 200s).
- Missing workspace / missing role non-system identities are denied.
- Role floor: viewer reads everything in their own workspace; the five
  mutating endpoints (commit, update, delete, publish, rollback) require
  editor; editor and admin pass; only explicit system scope bypasses.
- Regression anchors: publish and publication history keep their receipt
  semantics (require-gate block, force bypass receipt, gate_off outcome,
  workspace-isolated history) under the tightened boundary.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Plain import (no direct use): registers the channels table in the test
# metadata so delete_version's channel-pin query has its table in the
# lazily-created single-file schema.
import hecate.models.channels  # noqa: F401, E501
from hecate.core.auth_context import AuthContext
from hecate.models.agent import AgentModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.studio.agents.versioning import AgentVersionService


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> tuple[WorkspaceModel, AuthContext]:
    """Create org + workspace + member and return (workspace, context).

    Slugs carry a random suffix: API sessions commit through the shared
    connection, so rows survive a test's ``db_session`` rollback and slug
    collisions would leak between tests.
    """
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    org = OrganizationModel(name=f"Org {name}", slug=f"org-{name}", owner_id=user_id)
    db.add(org)
    await db.flush()
    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=role))
    await db.flush()
    # Durability against dependency-teardown rollbacks: boundary rejections
    # raise HTTPException and the API session's rollback would otherwise
    # wipe everything seeded on the shared connection.
    await db.commit()
    ctx = AuthContext(
        user_id=user_id,
        org_id=org.id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )
    return ws, ctx


async def _member_context(db: AsyncSession, ws: WorkspaceModel, role: WorkspaceRole) -> AuthContext:
    """Add a new member with ``role`` to an existing workspace."""
    user_id = uuid.uuid4()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=role))
    await db.commit()
    return AuthContext(
        user_id=user_id,
        org_id=ws.org_id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


def _system_context() -> AuthContext:
    """Deploy-time bootstrap identity: the only cross-workspace bypass."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


def _no_workspace_context() -> AuthContext:
    """Authenticated but with no active workspace (never system scope)."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


def _no_role_context(ws: WorkspaceModel) -> AuthContext:
    """Active workspace without any membership role."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


@contextlib.asynccontextmanager
async def _client_as(client: AsyncClient, ctx: AuthContext) -> AsyncIterator[AsyncClient]:
    """Yield a client authenticating as ``ctx``; restore the app override."""
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    async def override_auth() -> AuthContext:
        return ctx

    saved = app.dependency_overrides.get(get_auth_context)
    app.dependency_overrides[get_auth_context] = override_auth
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        if saved is None:
            app.dependency_overrides.pop(get_auth_context, None)
        else:
            app.dependency_overrides[get_auth_context] = saved


async def _seed_agent(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    versions: int = 1,
    publish: bool = False,
) -> AgentModel:
    """Seed an agent with committed versions via the trusted internal path."""
    agent = AgentModel(name="Boundary Agent", workspace_id=workspace_id, mode="chat")
    agent.model_config_db = {"model": "gpt-4o"}
    db.add(agent)
    await db.flush()
    service = AgentVersionService(db)
    for _ in range(versions):
        await service.commit_version(agent.id, name="seeded")
    if publish:
        await service.publish_version(agent.id, versions)
    await db.commit()
    return agent


def _read_calls(agent_id: uuid.UUID, version: int) -> list[tuple[str, str]]:
    """All eight read endpoints, as (method, url) pairs."""
    return [
        ("GET", f"/api/agents/{agent_id}/versions"),
        ("GET", f"/api/agents/{agent_id}/versions/{version}"),
        ("GET", f"/api/agents/{agent_id}/versions/{version}/skill-closure"),
        ("GET", f"/api/agents/{agent_id}/versions/{version}/drift"),
        ("GET", f"/api/agents/{agent_id}/publications"),
        ("GET", f"/api/agents/{agent_id}/published"),
        ("GET", f"/api/agents/{agent_id}/diff?v1=1&v2={version}"),
        ("GET", f"/api/agents/{agent_id}/version-status"),
    ]


def _write_calls(agent_id: uuid.UUID, version: int) -> list[tuple[str, str, dict[str, Any] | None]]:
    """All five mutating endpoints, as (method, url, json body) triples."""
    return [
        ("POST", f"/api/agents/{agent_id}/versions/commit", {}),
        ("PATCH", f"/api/agents/{agent_id}/versions/{version}", {"name": "renamed"}),
        ("DELETE", f"/api/agents/{agent_id}/versions/{version}", None),
        ("POST", f"/api/agents/{agent_id}/publish/{version}", {}),
        ("POST", f"/api/agents/{agent_id}/rollback/{version}", {}),
    ]


async def _call(http: AsyncClient, method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    if method == "GET":
        return await http.get(url)
    if method == "POST":
        return await http.post(url, json=body)
    if method == "PATCH":
        return await http.patch(url, json=body)
    if method == "DELETE":
        return await http.delete(url)
    raise AssertionError(f"unexpected method {method}")


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


async def _state_snapshot(db: AsyncSession, agent: AgentModel) -> dict[str, Any]:
    """Full observable lifecycle state, read via the trusted internal path."""
    service = AgentVersionService(db)
    versions = await service.list_versions(agent.id)
    publications = await service.list_publications(agent.id, auth_context=_system_context())
    return {
        "versions": versions,
        "contents": [await service.get_version(agent.id, item["version"], include_snapshot=True) for item in versions],
        "status": await service.get_version_status(agent.id),
        "publications": [item.model_dump(mode="json") for item in publications["items"]],
        "publications_total": publications["total"],
    }


# ---------------------------------------------------------------------------
# Workspace ownership (doubles as the per-endpoint identity-pass guard)
# ---------------------------------------------------------------------------


async def test_cross_workspace_reads_surface_as_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """All eight read endpoints hide other-workspace agents entirely."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, versions=1, publish=True)

    async with _client_as(client, outsider_ctx) as http:
        for method, url in _read_calls(agent.id, 1):
            resp = await _call(http, method, url)
            _assert_not_found(resp)
            assert " Boundary Agent" not in resp.text
            assert "seeded" not in resp.text

        # Same shape as a truly nonexistent agent: no existence leak.
        missing = f"/api/agents/{uuid.uuid4()}/versions"
        reference = await http.get(missing)
        probe = await http.get(f"/api/agents/{agent.id}/versions")
        assert probe.status_code == reference.status_code
        assert probe.json()["detail"]["error"]["code"] == reference.json()["detail"]["error"]["code"]


async def test_cross_workspace_writes_change_nothing(client: AsyncClient, db_session: AsyncSession) -> None:
    """All five mutating endpoints 404 across workspaces and leave no trace."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, versions=1, publish=True)
    before = await _state_snapshot(db_session, agent)

    async with _client_as(client, outsider_ctx) as http:
        for method, url, body in _write_calls(agent.id, 1):
            _assert_not_found(await _call(http, method, url, body))

    after = await _state_snapshot(db_session, agent)
    assert after == before


async def test_missing_workspace_identity_is_denied(client: AsyncClient, db_session: AsyncSession) -> None:
    """A workspace-less non-system identity never degrades to global access."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, versions=1)
    ctx = _no_workspace_context()

    async with _client_as(client, ctx) as http:
        for method, url in _read_calls(agent.id, 1):
            _assert_not_found(await _call(http, method, url))
        for method, url, body in _write_calls(agent.id, 1):
            _assert_not_found(await _call(http, method, url, body))


# ---------------------------------------------------------------------------
# Role floor
# ---------------------------------------------------------------------------


async def test_viewer_reads_own_workspace_and_cannot_write(client: AsyncClient, db_session: AsyncSession) -> None:
    """Viewer sees everything in their workspace; every write is 403."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    agent = await _seed_agent(db_session, ws.id, versions=1, publish=True)
    before = await _state_snapshot(db_session, agent)

    async with _client_as(client, viewer_ctx) as http:
        for method, url in _read_calls(agent.id, 1):
            resp = await _call(http, method, url)
            assert resp.status_code == 200, (url, resp.text)

        for method, url, body in _write_calls(agent.id, 1):
            _assert_forbidden(await _call(http, method, url, body))

    after = await _state_snapshot(db_session, agent)
    assert after == before


async def test_membership_without_role_is_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    """Same workspace but no role at all: reads and writes are both 403."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, versions=1)
    ctx = _no_role_context(ws)

    async with _client_as(client, ctx) as http:
        for method, url in _read_calls(agent.id, 1):
            _assert_forbidden(await _call(http, method, url))
        for method, url, body in _write_calls(agent.id, 1):
            _assert_forbidden(await _call(http, method, url, body))


async def test_editor_and_admin_can_write(client: AsyncClient, db_session: AsyncSession) -> None:
    """Editor exercises all five mutations; admin commits on top."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    admin_ctx = await _member_context(db_session, ws, WorkspaceRole.ADMIN)
    agent = await _seed_agent(db_session, ws.id, versions=1)

    async with _client_as(client, editor_ctx) as http:
        committed = await http.post(f"/api/agents/{agent.id}/versions/commit", json={})
        assert committed.status_code == 200, committed.text
        v2 = committed.json()["version"]

        renamed = await http.patch(f"/api/agents/{agent.id}/versions/1", json={"name": "renamed"})
        assert renamed.status_code == 200, renamed.text

        published = await http.post(f"/api/agents/{agent.id}/publish/1", json={})
        assert published.status_code == 200, published.text

        rollback = await http.post(f"/api/agents/{agent.id}/rollback/1", json={})
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["version"] == v2 + 1

        deleted = await http.delete(f"/api/agents/{agent.id}/versions/{v2}")
        assert deleted.status_code == 204, deleted.text

    async with _client_as(client, admin_ctx) as http:
        admin_commit = await http.post(f"/api/agents/{agent.id}/versions/commit", json={})
        assert admin_commit.status_code == 200, admin_commit.text


async def test_system_scope_bypasses_boundary(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only explicit system scope crosses workspaces — reads and writes."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, versions=1, publish=True)
    ctx = _system_context()

    async with _client_as(client, ctx) as http:
        for method, url in _read_calls(agent.id, 1):
            resp = await _call(http, method, url)
            assert resp.status_code == 200, (url, resp.text)

        committed = await http.post(f"/api/agents/{agent.id}/versions/commit", json={})
        assert committed.status_code == 200, committed.text
        published = await http.post(f"/api/agents/{agent.id}/publish/1", json={})
        assert published.status_code == 200, published.text


# ---------------------------------------------------------------------------
# Publish / history regression anchors (receipt semantics unchanged)
# ---------------------------------------------------------------------------


async def test_require_gate_block_and_force_receipt_survive_boundary(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Publish keeps gate semantics: require blocks, force writes a receipt."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, viewer_ctx = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    agent = await _seed_agent(db_session, ws.id, versions=1)

    async with _client_as(client, editor_ctx) as http:
        gate = await http.put(
            f"/api/agents/{agent.id}",
            json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
        )
        assert gate.status_code == 200, gate.text

        blocked = await http.post(f"/api/agents/{agent.id}/publish/1", json={})
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["error"]["code"] == "EVALUATION_GATE_BLOCKED"

        forced = await http.post(f"/api/agents/{agent.id}/publish/1", json={"force": True})
        assert forced.status_code == 200, forced.text

    async with _client_as(client, viewer_ctx) as http:
        _assert_not_found(await http.get(f"/api/agents/{agent.id}/publications"))

    async with _client_as(client, editor_ctx) as http:
        history = await http.get(f"/api/agents/{agent.id}/publications")
        assert history.status_code == 200, history.text
        body = history.json()
        assert body["total"] == 1
        assert body["items"][0]["outcome"] == "force_bypassed"
        assert body["items"][0]["candidate_run_id"] is None


async def test_gate_off_receipt_outcome_unchanged(client: AsyncClient, db_session: AsyncSession) -> None:
    """Publishing with the gate off still records gate_off, never 'passed'."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, versions=1)

    async with _client_as(client, editor_ctx) as http:
        published = await http.post(f"/api/agents/{agent.id}/publish/1", json={})
        assert published.status_code == 200, published.text
        history = await http.get(f"/api/agents/{agent.id}/publications")
        assert history.status_code == 200, history.text
        assert history.json()["items"][0]["outcome"] == "gate_off"


async def test_publication_history_missing_context_remains_fail_closed(db_session: AsyncSession) -> None:
    """The existing receipt query never treats omitted identity as global access."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    agent = await _seed_agent(db_session, ws.id, publish=True)
    with pytest.raises(ValueError, match=f"Agent {agent.id} not found"):
        await AgentVersionService(db_session).list_publications(agent.id)


async def test_viewer_cannot_force_publish(client: AsyncClient, db_session: AsyncSession) -> None:
    """Force bypasses evaluation, never the editor role requirement."""
    ws, viewer = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    agent = await _seed_agent(db_session, ws.id)
    before = await _state_snapshot(db_session, agent)
    async with _client_as(client, viewer) as http:
        _assert_forbidden(await http.post(f"/api/agents/{agent.id}/publish/1", json={"force": True}))
    assert await _state_snapshot(db_session, agent) == before


def test_lifecycle_access_matrix_covers_every_registered_route() -> None:
    """New lifecycle routes must be added to both the identity and role matrix."""
    from fastapi.routing import APIRoute

    from hecate.studio.api.agent_versions import router

    agent_id = uuid.uuid4()
    calls = [(method, url) for method, url in _read_calls(agent_id, 1)]
    calls += [(method, url) for method, url, _body in _write_calls(agent_id, 1)]
    covered = set()
    for method, url in calls:
        segments = url.split("?", 1)[0].removeprefix("/api").split("/")
        normalized = [
            "{agent_id}" if value == str(agent_id) else "{version}" if value == "1" else value for value in segments
        ]
        covered.add((method, "/".join(normalized)))
    registered = {
        (method, route.path) for route in router.routes if isinstance(route, APIRoute) for method in route.methods
    }
    assert covered == registered
