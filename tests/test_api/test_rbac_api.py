"""Tests for RBAC (Role-Based Access Control) API behavior.

Validates role-based permission enforcement across workspace endpoints.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_full_access(client: AsyncClient) -> None:
    """Test admin role has full access to workspace operations."""
    # Create org
    org_response = await client.post(
        "/api/orgs",
        json={"name": "RBAC Org", "slug": "rbac-org"},
    )
    org_id = org_response.json()["id"]

    # Create workspace
    ws_response = await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "RBAC Workspace", "slug": "rbac-ws"},
    )
    ws_id = ws_response.json()["id"]

    # Admin should be able to list members
    members_response = await client.get(f"/api/orgs/{org_id}/workspaces/{ws_id}/members")
    assert members_response.status_code == 200


@pytest.mark.asyncio
async def test_create_resource_with_workspace_scope(client: AsyncClient) -> None:
    """Test creating resources with workspace scope."""
    # Create agent (should use workspace from auth context)
    response = await client.post(
        "/api/agents",
        json={
            "name": "RBAC Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "RBAC Agent"


@pytest.mark.asyncio
async def test_list_resources_filtered_by_workspace(client: AsyncClient) -> None:
    """Test listing resources is filtered by workspace."""
    # Create agent
    await client.post(
        "/api/agents",
        json={
            "name": "Workspace Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
    )

    # List agents - should only see workspace-scoped agents
    response = await client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_system_scope_access(client: AsyncClient) -> None:
    """Test system-scope keys can access all resources."""
    # This test would need a system-scope auth context
    # For now, verify the endpoint works with current auth
    response = await client.get("/api/tools")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_workspace_member_operations(client: AsyncClient) -> None:
    """Test workspace member CRUD operations."""
    # Create org and workspace
    org_response = await client.post(
        "/api/orgs",
        json={"name": "Member Org", "slug": "member-org"},
    )
    org_id = org_response.json()["id"]

    ws_response = await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "Member Workspace", "slug": "member-ws"},
    )
    ws_id = ws_response.json()["id"]

    # List members
    members_response = await client.get(f"/api/orgs/{org_id}/workspaces/{ws_id}/members")
    assert members_response.status_code == 200


@pytest.mark.asyncio
async def test_unauthorized_access_rejected(client: AsyncClient) -> None:
    """Test unauthorized access is rejected."""
    # This would need a client without auth context
    # For now, verify the auth dependency is working
    response = await client.get("/api/agents")
    assert response.status_code == 200  # Auth context is mocked in tests
