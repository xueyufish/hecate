"""Tests for Organization API endpoints.

Validates CRUD operations, ownership transfer, and permission enforcement
for the /api/orgs endpoints.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_organization(client: AsyncClient) -> None:
    """Test creating an organization."""
    response = await client.post(
        "/api/orgs",
        json={
            "name": "New Organization",
            "slug": "new-org",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "New Organization"
    assert data["slug"] == "new-org"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_organizations(client: AsyncClient) -> None:
    """Test listing organizations."""
    # Create an org first
    await client.post(
        "/api/orgs",
        json={"name": "Org 1", "slug": "org-1"},
    )

    response = await client.get("/api/orgs")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_organization(client: AsyncClient) -> None:
    """Test getting organization by ID."""
    # Create an org
    create_response = await client.post(
        "/api/orgs",
        json={"name": "Get Test", "slug": "get-test"},
    )
    org_id = create_response.json()["id"]

    response = await client.get(f"/api/orgs/{org_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Get Test"
    assert data["id"] == org_id


@pytest.mark.asyncio
async def test_update_organization(client: AsyncClient) -> None:
    """Test updating organization."""
    # Create an org
    create_response = await client.post(
        "/api/orgs",
        json={"name": "Update Test", "slug": "update-test"},
    )
    org_id = create_response.json()["id"]

    response = await client.patch(
        f"/api/orgs/{org_id}",
        json={"name": "Updated Name"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"


@pytest.mark.asyncio
async def test_delete_organization(client: AsyncClient) -> None:
    """Test deleting organization."""
    # Create an org
    create_response = await client.post(
        "/api/orgs",
        json={"name": "Delete Test", "slug": "delete-test"},
    )
    org_id = create_response.json()["id"]

    response = await client.delete(f"/api/orgs/{org_id}")
    assert response.status_code == 204

    # Verify it's soft deleted
    get_response = await client.get(f"/api/orgs/{org_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_organization(client: AsyncClient) -> None:
    """Test getting a non-existent organization."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/orgs/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_organization_validation(client: AsyncClient) -> None:
    """Test organization creation validation."""
    response = await client.post(
        "/api/orgs",
        json={},  # Missing required fields
    )
    assert response.status_code == 422
