"""Tests for workflow API endpoints: validate, test-run, version bumping."""

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

LINEAR_DSL = {
    "version": "1.0",
    "name": "linear",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
    },
    "edges": [{"source": "A", "target": "B"}],
    "entry": "A",
}

NEW_TYPE_DSL = {
    "version": "1.0",
    "name": "new-types",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "retrieve": {
            "type": "knowledge-retrieval",
            "config": {"kb_ids": ["kb-1"], "query_template": "{{query}}", "top_k": 5},
        },
        "set_var": {
            "type": "variable-set",
            "config": {"variable_name": "result", "value": "done"},
        },
    },
    "edges": [{"source": "retrieve", "target": "set_var"}],
    "entry": "retrieve",
}


@pytest.mark.asyncio
async def test_validate_valid_dsl(client: AsyncClient) -> None:
    """Test POST /validate returns valid=true for well-formed DSL."""
    create_resp = await client.post("/api/workflows", json={"name": "val-test", "graph_dsl": VALID_DSL})
    wf_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/workflows/{wf_id}/validate",
        json={"graph_dsl": LINEAR_DSL},
    )
    assert response.status_code == 200

    result = response.json()
    assert result["valid"] is True
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_validate_invalid_dsl(client: AsyncClient) -> None:
    """Test POST /validate returns errors for broken DSL."""
    create_resp = await client.post("/api/workflows", json={"name": "val-invalid", "graph_dsl": VALID_DSL})
    wf_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/workflows/{wf_id}/validate",
        json={"graph_dsl": {"bad": "dsl"}},
    )
    assert response.status_code == 200

    result = response.json()
    assert result["valid"] is False
    assert len(result["errors"]) > 0


@pytest.mark.asyncio
async def test_validate_new_node_types(client: AsyncClient) -> None:
    """Test POST /validate accepts knowledge-retrieval and variable-set nodes."""
    create_resp = await client.post("/api/workflows", json={"name": "new-types", "graph_dsl": VALID_DSL})
    wf_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/workflows/{wf_id}/validate",
        json={"graph_dsl": NEW_TYPE_DSL},
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True


@pytest.mark.asyncio
async def test_test_run_mock_mode(client: AsyncClient) -> None:
    """Test POST /test-run with mock=true returns per-node results."""
    create_resp = await client.post("/api/workflows", json={"name": "run-test", "graph_dsl": LINEAR_DSL})
    wf_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/workflows/{wf_id}/test-run",
        json={"input_data": {"messages": [{"role": "user", "content": "hi"}]}, "mock": True},
    )
    assert response.status_code == 200

    result = response.json()
    assert result["status"] == "completed"
    assert result["run_id"] is not None
    assert len(result["nodes"]) >= 2

    node_ids = {n["node_id"] for n in result["nodes"]}
    assert "A" in node_ids
    assert "B" in node_ids

    for node in result["nodes"]:
        if node["node_id"] in ("A", "B"):
            assert node["status"] in ("completed", "skipped")


@pytest.mark.asyncio
async def test_test_run_not_found(client: AsyncClient) -> None:
    """Test POST /test-run returns 404 for non-existent workflow."""
    response = await client.post(
        f"/api/workflows/{uuid.uuid4()}/test-run",
        json={"input_data": {}, "mock": True},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_version_bumps_on_dsl_change(client: AsyncClient) -> None:
    """Test that updating graph_dsl creates a new version."""
    create_resp = await client.post("/api/workflows", json={"name": "ver-test", "graph_dsl": VALID_DSL})
    wf_id = create_resp.json()["id"]
    assert create_resp.json()["current_version"] == 1

    # Update DSL — should create version 2
    update_resp = await client.put(
        f"/api/workflows/{wf_id}",
        json={"graph_dsl": LINEAR_DSL},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["current_version"] == 2

    # Update name only — should NOT create a new version
    name_resp = await client.put(
        f"/api/workflows/{wf_id}",
        json={"name": "renamed"},
    )
    assert name_resp.status_code == 200
    assert name_resp.json()["current_version"] == 2
    assert name_resp.json()["name"] == "renamed"


@pytest.mark.asyncio
async def test_version_history_after_updates(client: AsyncClient) -> None:
    """Test version history reflects all DSL changes."""
    create_resp = await client.post("/api/workflows", json={"name": "hist-test", "graph_dsl": VALID_DSL})
    wf_id = create_resp.json()["id"]

    await client.put(f"/api/workflows/{wf_id}", json={"graph_dsl": LINEAR_DSL})

    versions_resp = await client.get(f"/api/workflows/{wf_id}/versions")
    assert versions_resp.status_code == 200

    versions = versions_resp.json()
    assert len(versions) == 2
    assert versions[0]["version"] in (1, 2)
    assert versions[1]["version"] in (1, 2)
