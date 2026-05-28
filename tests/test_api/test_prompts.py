"""Integration tests for prompt API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_prompt(client: AsyncClient) -> None:
    """Test POST /api/prompts creates a prompt."""
    data = {
        "name": "test-prompt",
        "template": "Hello {{ name }}!",
        "variables": ["name"],
        "labels": ["production"],
    }

    response = await client.post("/api/prompts", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["name"] == "test-prompt"
    assert result["current_version"] == 1
    assert result["version"]["template"] == "Hello {{ name }}!"


@pytest.mark.asyncio
async def test_create_prompt_invalid_template(client: AsyncClient) -> None:
    """Test POST /api/prompts with invalid template returns 422."""
    data = {
        "name": "test",
        "template": "Hello {{ name !",
    }

    response = await client.post("/api/prompts", json=data)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_prompts(client: AsyncClient) -> None:
    """Test GET /api/prompts lists prompts."""
    await client.post("/api/prompts", json={"name": "p1", "template": "T1"})
    await client.post("/api/prompts", json={"name": "p2", "template": "T2"})

    response = await client.get("/api/prompts")
    assert response.status_code == 200

    result = response.json()
    assert "items" in result
    assert result["total"] >= 2


@pytest.mark.asyncio
async def test_get_prompt(client: AsyncClient) -> None:
    """Test GET /api/prompts/{id} returns prompt."""
    create_resp = await client.post("/api/prompts", json={"name": "test", "template": "T1"})
    prompt_id = create_resp.json()["id"]

    response = await client.get(f"/api/prompts/{prompt_id}")
    assert response.status_code == 200

    result = response.json()
    assert result["id"] == prompt_id
    assert result["name"] == "test"


@pytest.mark.asyncio
async def test_get_prompt_not_found(client: AsyncClient) -> None:
    """Test GET /api/prompts/{id} returns 404 for non-existent."""
    response = await client.get(f"/api/prompts/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_prompt(client: AsyncClient) -> None:
    """Test PUT /api/prompts/{id} updates prompt."""
    create_resp = await client.post("/api/prompts", json={"name": "test", "template": "T1"})
    prompt_id = create_resp.json()["id"]

    response = await client.put(f"/api/prompts/{prompt_id}", json={"template": "T2"})
    assert response.status_code == 200

    result = response.json()
    assert result["current_version"] == 2


@pytest.mark.asyncio
async def test_delete_prompt(client: AsyncClient) -> None:
    """Test DELETE /api/prompts/{id} deletes prompt."""
    create_resp = await client.post("/api/prompts", json={"name": "test", "template": "T1"})
    prompt_id = create_resp.json()["id"]

    response = await client.delete(f"/api/prompts/{prompt_id}")
    assert response.status_code == 204

    # Verify deleted
    get_resp = await client.get(f"/api/prompts/{prompt_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_prompt_versions(client: AsyncClient) -> None:
    """Test GET /api/prompts/{id}/versions lists versions."""
    create_resp = await client.post("/api/prompts", json={"name": "test", "template": "V1"})
    prompt_id = create_resp.json()["id"]

    await client.put(f"/api/prompts/{prompt_id}", json={"template": "V2"})

    response = await client.get(f"/api/prompts/{prompt_id}/versions")
    assert response.status_code == 200

    versions = response.json()
    assert len(versions) == 2


@pytest.mark.asyncio
async def test_rollback_prompt(client: AsyncClient) -> None:
    """Test POST /api/prompts/{id}/rollback/{version} rolls back."""
    create_resp = await client.post("/api/prompts", json={"name": "test", "template": "V1"})
    prompt_id = create_resp.json()["id"]

    await client.put(f"/api/prompts/{prompt_id}", json={"template": "V2"})

    response = await client.post(f"/api/prompts/{prompt_id}/rollback/1")
    assert response.status_code == 200

    result = response.json()
    assert result["current_version"] == 3


@pytest.mark.asyncio
async def test_get_prompt_by_label(client: AsyncClient) -> None:
    """Test GET /api/prompts/by-label/{label} returns prompt."""
    await client.post(
        "/api/prompts",
        json={
            "name": "test",
            "template": "T1",
            "labels": ["production"],
        },
    )

    response = await client.get("/api/prompts/by-label/production")
    assert response.status_code == 200

    result = response.json()
    assert result["name"] == "test"


@pytest.mark.asyncio
async def test_get_prompt_by_label_not_found(client: AsyncClient) -> None:
    """Test GET /api/prompts/by-label/{label} returns 404."""
    response = await client.get("/api/prompts/by-label/nonexistent")
    assert response.status_code == 404
