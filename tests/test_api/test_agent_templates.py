"""Tests for agent template API endpoints."""

from __future__ import annotations

from httpx import AsyncClient


async def test_list_templates(client: AsyncClient) -> None:
    """Test GET /api/agent-templates returns template list."""
    response = await client.get("/api/agent-templates")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) > 0

    first = data["items"][0]
    assert "id" in first
    assert "name" in first
    assert "description" in first
    assert "category" in first


async def test_get_template(client: AsyncClient) -> None:
    """Test GET /api/agent-templates/{id} returns full template."""
    response = await client.get("/api/agent-templates/customer-service")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "customer-service"
    assert "config" in data
    assert "persona" in data["config"]


async def test_get_template_not_found(client: AsyncClient) -> None:
    """Test GET /api/agent-templates/{id} returns 404 for non-existent."""
    response = await client.get("/api/agent-templates/non-existent")
    assert response.status_code == 404


async def test_instantiate_template(client: AsyncClient) -> None:
    """Test POST /api/agent-templates/{id}/instantiate returns config."""
    response = await client.post("/api/agent-templates/customer-service/instantiate")
    assert response.status_code == 200
    data = response.json()
    assert "persona" in data
    assert "model_config" in data
    assert data["mode"] == "chat"


async def test_instantiate_template_with_valid_kb(client: AsyncClient) -> None:
    """Test instantiation with valid KB IDs succeeds."""
    kb_resp = await client.post("/api/knowledge-bases", json={"name": "test-kb"})
    assert kb_resp.status_code == 201

    response = await client.post(
        "/api/agent-templates/customer-service/instantiate",
    )
    assert response.status_code == 200


async def test_instantiate_template_not_found(client: AsyncClient) -> None:
    """Test instantiation returns 404 for non-existent template."""
    response = await client.post("/api/agent-templates/non-existent/instantiate")
    assert response.status_code == 404
