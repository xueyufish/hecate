"""Tests for agent import/export endpoints."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def test_export_agent(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/export returns export data."""
    create_resp = await client.post(
        "/api/agents",
        json={
            "name": "Test Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
            "persona": "You are helpful",
        },
    )
    assert create_resp.status_code == 201
    agent_id = create_resp.json()["id"]

    response = await client.get(f"/api/agents/{agent_id}/export")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "exported_at" in data
    assert data["agent"]["name"] == "Test Agent"
    assert data["agent"]["persona"] == "You are helpful"


async def test_export_agent_not_found(client: AsyncClient) -> None:
    """Test export returns 404 for non-existent agent."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/api/agents/{fake_id}/export")
    assert response.status_code == 404


async def test_export_agent_with_memory_blocks(client: AsyncClient) -> None:
    """Test export includes memory blocks."""
    create_resp = await client.post(
        "/api/agents",
        json={
            "name": "Agent With Memory",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
    )
    assert create_resp.status_code == 201
    agent_id = create_resp.json()["id"]

    await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={"label": "persona", "content": "You are helpful", "position": 0},
    )

    response = await client.get(f"/api/agents/{agent_id}/export")
    assert response.status_code == 200
    data = response.json()
    assert "memory_blocks" in data
    assert len(data["memory_blocks"]) == 1
    assert data["memory_blocks"][0]["label"] == "persona"


async def test_import_agent(client: AsyncClient) -> None:
    """Test POST /api/agents/import creates agent from export data."""
    import_data = {
        "version": "1.0",
        "exported_at": "2026-01-01T00:00:00Z",
        "agent": {
            "name": "Imported Agent",
            "persona": "Imported persona",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
            "tools": [],
            "skills": [],
            "knowledge_base_ids": [],
            "risk_level": "LOW",
        },
    }

    response = await client.post("/api/agents/import", json=import_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Imported Agent"
    assert data["persona"] == "Imported persona"


async def test_import_agent_with_memory_blocks(client: AsyncClient) -> None:
    """Test import creates memory blocks."""
    import_data = {
        "version": "1.0",
        "agent": {
            "name": "Agent With Blocks",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
        "memory_blocks": [
            {"label": "persona", "content": "You are helpful", "position": 0, "limit": 2000},
        ],
    }

    response = await client.post("/api/agents/import", json=import_data)
    assert response.status_code == 201
    agent_id = response.json()["id"]

    blocks_resp = await client.get(f"/api/agents/{agent_id}/memory-blocks")
    assert blocks_resp.status_code == 200
    blocks = blocks_resp.json()
    assert len(blocks) == 1
    assert blocks[0]["label"] == "persona"


async def test_import_agent_invalid_json(client: AsyncClient) -> None:
    """Test import returns 422 for invalid JSON."""
    response = await client.post("/api/agents/import", json={"invalid": "data"})
    assert response.status_code == 422
