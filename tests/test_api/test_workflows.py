"""Integration tests for workflow API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

VALID_DSL = {
    "version": "1.0",
    "name": "test",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}


@pytest.mark.asyncio
async def test_create_workflow(client: AsyncClient) -> None:
    """Test POST /api/workflows creates a workflow."""
    data = {"name": "test-workflow", "graph_dsl": VALID_DSL}

    response = await client.post("/api/workflows", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["name"] == "test-workflow"
    assert result["current_version"] == 1
    assert result["version"]["version"] == 1


@pytest.mark.asyncio
async def test_create_workflow_invalid_dsl(client: AsyncClient) -> None:
    """Test POST /api/workflows with invalid DSL returns 422."""
    data = {"name": "test", "graph_dsl": {"invalid": "dsl"}}

    response = await client.post("/api/workflows", json=data)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_workflows(client: AsyncClient) -> None:
    """Test GET /api/workflows lists workflows."""
    create_data = {"name": "test", "graph_dsl": VALID_DSL}
    await client.post("/api/workflows", json=create_data)

    response = await client.get("/api/workflows")
    assert response.status_code == 200

    result = response.json()
    assert "items" in result
    assert "total" in result
    assert result["total"] >= 1


@pytest.mark.asyncio
async def test_get_workflow(client: AsyncClient) -> None:
    """Test GET /api/workflows/{id} returns workflow."""
    create_data = {"name": "test", "graph_dsl": VALID_DSL}
    create_resp = await client.post("/api/workflows", json=create_data)
    workflow_id = create_resp.json()["id"]

    response = await client.get(f"/api/workflows/{workflow_id}")
    assert response.status_code == 200

    result = response.json()
    assert result["id"] == workflow_id
    assert result["name"] == "test"


@pytest.mark.asyncio
async def test_get_workflow_not_found(client: AsyncClient) -> None:
    """Test GET /api/workflows/{id} returns 404 for non-existent."""
    response = await client.get(f"/api/workflows/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_workflow(client: AsyncClient) -> None:
    """Test PUT /api/workflows/{id} updates workflow."""
    create_data = {"name": "original", "graph_dsl": VALID_DSL}
    create_resp = await client.post("/api/workflows", json=create_data)
    workflow_id = create_resp.json()["id"]

    update_data = {"name": "updated"}
    response = await client.put(f"/api/workflows/{workflow_id}", json=update_data)
    assert response.status_code == 200

    result = response.json()
    assert result["name"] == "updated"


@pytest.mark.asyncio
async def test_delete_workflow(client: AsyncClient) -> None:
    """Test DELETE /api/workflows/{id} deletes workflow."""
    create_data = {"name": "to-delete", "graph_dsl": VALID_DSL}
    create_resp = await client.post("/api/workflows", json=create_data)
    workflow_id = create_resp.json()["id"]

    response = await client.delete(f"/api/workflows/{workflow_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/workflows/{workflow_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_workflow_not_found(client: AsyncClient) -> None:
    """Test DELETE /api/workflows/{id} returns 404 for non-existent."""
    response = await client.delete(f"/api/workflows/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_workflow_versions(client: AsyncClient) -> None:
    """Test GET /api/workflows/{id}/versions lists versions."""
    create_data = {"name": "test", "graph_dsl": VALID_DSL}
    create_resp = await client.post("/api/workflows", json=create_data)
    workflow_id = create_resp.json()["id"]

    response = await client.get(f"/api/workflows/{workflow_id}/versions")
    assert response.status_code == 200

    versions = response.json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1


@pytest.mark.asyncio
async def test_get_workflow_version(client: AsyncClient) -> None:
    """Test GET /api/workflows/{id}/versions/{version} returns version."""
    create_data = {"name": "test", "graph_dsl": VALID_DSL}
    create_resp = await client.post("/api/workflows", json=create_data)
    workflow_id = create_resp.json()["id"]

    response = await client.get(f"/api/workflows/{workflow_id}/versions/1")
    assert response.status_code == 200

    result = response.json()
    assert result["version"] == 1


@pytest.mark.asyncio
async def test_rollback_workflow(client: AsyncClient) -> None:
    """Test POST /api/workflows/{id}/rollback/{version} rolls back."""
    create_data = {"name": "test", "graph_dsl": VALID_DSL}
    create_resp = await client.post("/api/workflows", json=create_data)
    workflow_id = create_resp.json()["id"]

    update_data = {
        "graph_dsl": {
            "version": "1.0",
            "name": "v2",
            "state": {"messages": {"type": "topic", "reduce": "append"}},
            "nodes": {
                "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
                "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
            },
            "edges": [{"source": "A", "target": "B"}],
            "entry": "A",
        },
    }
    await client.put(f"/api/workflows/{workflow_id}", json=update_data)

    response = await client.post(f"/api/workflows/{workflow_id}/rollback/1")
    assert response.status_code == 200

    result = response.json()
    assert result["current_version"] == 3
