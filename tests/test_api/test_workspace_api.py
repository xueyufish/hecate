"""Tests for Workspace API endpoints.

Validates CRUD operations and workspace-scoped resource management
for the /api/orgs/{org_id}/workspaces endpoints.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_workspace(client: AsyncClient) -> None:
    """Test creating a workspace."""
    # Use the default org from fixtures
    from tests.conftest import DEFAULT_WORKSPACE_ID

    org_id = str(DEFAULT_WORKSPACE_ID)

    response = await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "New Workspace", "slug": "new-ws"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Workspace"
    assert data["slug"] == "new-ws"
    assert data["org_id"] == org_id


@pytest.mark.asyncio
async def test_list_workspaces(client: AsyncClient) -> None:
    """Test listing workspaces for an org."""
    # Use the default org from fixtures
    from tests.conftest import DEFAULT_WORKSPACE_ID

    org_id = str(DEFAULT_WORKSPACE_ID)

    # Create workspace
    await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "Workspace 1", "slug": "ws-1"},
    )

    response = await client.get(f"/api/orgs/{org_id}/workspaces")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_workspace(client: AsyncClient) -> None:
    """Test getting workspace by ID."""
    # Use the default org from fixtures
    from tests.conftest import DEFAULT_WORKSPACE_ID

    org_id = str(DEFAULT_WORKSPACE_ID)

    # Create workspace
    ws_response = await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "Get Test", "slug": "get-test"},
    )
    ws_id = ws_response.json()["id"]

    response = await client.get(f"/api/orgs/{org_id}/workspaces/{ws_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Get Test"
    assert data["id"] == ws_id


@pytest.mark.asyncio
async def test_update_workspace(client: AsyncClient) -> None:
    """Test updating workspace."""
    # Use the default org from fixtures
    from tests.conftest import DEFAULT_WORKSPACE_ID

    org_id = str(DEFAULT_WORKSPACE_ID)

    # Create workspace
    ws_response = await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "Update Test", "slug": "update-test"},
    )
    ws_id = ws_response.json()["id"]

    response = await client.patch(
        f"/api/orgs/{org_id}/workspaces/{ws_id}",
        json={"name": "Updated Name"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_delete_workspace(client: AsyncClient) -> None:
    """Test deleting workspace."""
    # Use the default org from fixtures
    from tests.conftest import DEFAULT_WORKSPACE_ID

    org_id = str(DEFAULT_WORKSPACE_ID)

    # Create workspace
    ws_response = await client.post(
        f"/api/orgs/{org_id}/workspaces",
        json={"name": "Delete Test", "slug": "delete-test"},
    )
    ws_id = ws_response.json()["id"]

    response = await client.delete(f"/api/orgs/{org_id}/workspaces/{ws_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_get_nonexistent_workspace(client: AsyncClient) -> None:
    """Test getting a non-existent workspace."""
    from tests.conftest import DEFAULT_WORKSPACE_ID

    org_id = str(DEFAULT_WORKSPACE_ID)
    ws_id = str(uuid.uuid4())
    response = await client.get(f"/api/orgs/{org_id}/workspaces/{ws_id}")
    assert response.status_code == 404
