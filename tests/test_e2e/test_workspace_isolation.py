"""F7 — workspace isolation: same-named resources do not leak between tenants.

A missing ``WHERE workspace_id =`` filter anywhere in the read path turns
into a data-leak class bug — a customer sees another customer's agent,
session, or tool-policy rule. Without API-level two-workspace tests, this
class of bug is invisible to the test suite (no end-to-end check that the
list endpoint actually scopes by the AuthContext's workspace).

The test creates two workspaces with the same user, gives each its own
AuthContext, registers the same agent name in both, and asserts that
GET /api/agents under each context returns exactly one — and that
name — but the agents are different rows.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.core.auth_context import AuthContext
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


@pytest.fixture
async def second_workspace(db_session, default_org) -> WorkspaceModel:
    """A second workspace in the same org, with the test user as admin."""
    ws = WorkspaceModel(
        org_id=default_org.id,
        name="ws-tenant-b",
        slug=f"ws-tenant-b-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest.fixture
def auth_context_b(test_user_id, second_workspace) -> AuthContext:
    """AuthContext bound to the second workspace."""
    return AuthContext(
        user_id=test_user_id,
        org_id=second_workspace.org_id,
        workspace_id=second_workspace.id,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )


async def test_workspace_a_registers_member_for_second_workspace(db_session, test_user_id, second_workspace) -> None:
    """Test prerequisite: test_user is admin in workspace B too.

    Without this, AuthContext-B would carry a workspace_id the user
    doesn't actually belong to — the test would fail for the wrong
    reason (membership mismatch instead of scoping).
    """
    db_session.add(
        WorkspaceMemberModel(
            user_id=test_user_id,
            workspace_id=second_workspace.id,
            role=WorkspaceRole.ADMIN,
        )
    )
    await db_session.flush()


async def test_list_agents_is_workspace_scoped(client, auth_context_b, second_workspace, test_user_id) -> None:
    """Two workspaces, one agent named 'leaky' in each — each context must see only its own.

    Asserts the GET /api/agents filter actually applies. If the
    query drops the workspace_id filter, both contexts see both agents.
    """
    import json

    from hecate.api.management.agents import (
        router as _,  # noqa: F401  (imported to ensure router is registered on the app)
    )
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    # Capture the default workspace-A override the client fixture installed
    # so we can restore it after swapping to workspace B below.
    default_override = app.dependency_overrides.get(get_auth_context)

    def _restore_default() -> None:
        if default_override is not None:
            app.dependency_overrides[get_auth_context] = default_override
        else:
            app.dependency_overrides.pop(get_auth_context, None)

    async def override_b() -> AuthContext:
        return auth_context_b

    def install_override_b() -> None:
        app.dependency_overrides[get_auth_context] = override_b

    body = json.dumps(
        {
            "name": "leaky",
            "persona": "test",
            "llm_config": {"model": "gpt-4o"},
            "mode": "chat",
        }
    ).encode()

    # Create the agent in workspace A (default_workspace) via the client's
    # already-overridden auth_context fixture.
    response_a_create = await client.post("/api/agents", content=body, headers={"content-type": "application/json"})
    assert response_a_create.status_code == 201, response_a_create.text

    # The default `client` fixture overrides get_auth_context with the
    # workspace-A context; use app.dependency_overrides directly (NOT pop)
    # to switch to workspace B without disturbing the default.
    app.dependency_overrides[get_auth_context] = override_b
    try:
        response_b_create = await client.post("/api/agents", content=body, headers={"content-type": "application/json"})
        assert response_b_create.status_code == 201, response_b_create.text
        agent_b_id = response_b_create.json()["id"]
    finally:
        _restore_default()

    # List under workspace A's auth context (the default fixture override
    # is restored above).
    list_a = await client.get("/api/agents")
    assert list_a.status_code == 200
    items_a = list_a.json()["items"]
    leaky_in_a = [a for a in items_a if a["name"] == "leaky"]
    assert len(leaky_in_a) == 1, f"workspace A must see exactly one 'leaky', got {len(leaky_in_a)}"
    assert leaky_in_a[0]["id"] != agent_b_id, "workspace A's 'leaky' must not be workspace B's row"

    # List under workspace B's auth context.
    install_override_b()
    try:
        list_b = await client.get("/api/agents")
        assert list_b.status_code == 200
        items_b = list_b.json()["items"]
        leaky_in_b = [a for a in items_b if a["name"] == "leaky"]
        assert len(leaky_in_b) == 1, f"workspace B must see exactly one 'leaky', got {len(leaky_in_b)}"
        assert leaky_in_b[0]["id"] == agent_b_id
    finally:
        _restore_default()

    # Cross-check: A and B see different 'leaky' rows.
    assert leaky_in_a[0]["id"] != leaky_in_b[0]["id"]
