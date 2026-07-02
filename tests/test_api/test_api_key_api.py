"""Tests for API Key management endpoints.

Validates CRUD operations, key rotation, and scope enforcement
for the /api/api-keys endpoints.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_api_key(client: AsyncClient) -> None:
    """Test creating an API key."""
    from tests.conftest import DEFAULT_WORKSPACE_ID

    response = await client.post(
        "/api/api-keys",
        json={
            "name": "Test Key",
            "scope": "workspace",
            "workspace_id": str(DEFAULT_WORKSPACE_ID),
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Key"
    assert data["scope"] == "workspace"
    assert "id" in data
    assert "key_prefix" in data


@pytest.mark.asyncio
async def test_list_api_keys(client: AsyncClient) -> None:
    """Test listing API keys."""
    from tests.conftest import DEFAULT_WORKSPACE_ID

    # Create a key first
    await client.post(
        "/api/api-keys",
        json={"name": "List Test", "scope": "workspace", "workspace_id": str(DEFAULT_WORKSPACE_ID)},
    )

    response = await client.get("/api/api-keys")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_api_key(client: AsyncClient) -> None:
    """Test getting API key by ID."""
    from tests.conftest import DEFAULT_WORKSPACE_ID

    # Create a key
    create_response = await client.post(
        "/api/api-keys",
        json={"name": "Get Test", "scope": "workspace", "workspace_id": str(DEFAULT_WORKSPACE_ID)},
    )
    key_id = create_response.json()["id"]

    response = await client.get(f"/api/api-keys/{key_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Get Test"
    assert data["id"] == key_id


@pytest.mark.asyncio
async def test_delete_api_key(client: AsyncClient) -> None:
    """Test deleting API key."""
    from tests.conftest import DEFAULT_WORKSPACE_ID

    # Create a key
    create_response = await client.post(
        "/api/api-keys",
        json={"name": "Delete Test", "scope": "workspace", "workspace_id": str(DEFAULT_WORKSPACE_ID)},
    )
    key_id = create_response.json()["id"]

    response = await client.delete(f"/api/api-keys/{key_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_rotate_api_key(client: AsyncClient) -> None:
    """Test rotating API key."""
    from tests.conftest import DEFAULT_WORKSPACE_ID

    # Create a key
    create_response = await client.post(
        "/api/api-keys",
        json={"name": "Rotate Test", "scope": "workspace", "workspace_id": str(DEFAULT_WORKSPACE_ID)},
    )
    key_id = create_response.json()["id"]

    response = await client.post(f"/api/api-keys/{key_id}/rotate")
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "key" in data  # New key should be returned
    # The rotated key creates a new ID
    assert data["id"] != key_id  # New key has different ID


@pytest.mark.asyncio
async def test_create_system_scope_key(client: AsyncClient) -> None:
    """Test creating a system-scope API key."""
    response = await client.post(
        "/api/api-keys",
        json={
            "name": "System Key",
            "scope": "system",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["scope"] == "system"


@pytest.mark.asyncio
async def test_get_nonexistent_api_key(client: AsyncClient) -> None:
    """Test getting a non-existent API key."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/api-keys/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_api_key_validation(client: AsyncClient) -> None:
    """Test API key creation validation."""
    response = await client.post(
        "/api/api-keys",
        json={},  # Missing required fields
    )
    assert response.status_code == 422
