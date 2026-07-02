"""E2E tests for workflow canvas: create → edit → validate → test-run round-trip."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

SIMPLE_DSL = {
    "version": "1.0",
    "name": "simple",
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

CONDITION_DSL = {
    "version": "1.0",
    "name": "conditional",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "check": {"type": "condition", "config": {"expression": "true"}},
        "yes_path": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "no_path": {"type": "conversation", "config": {"model": "gpt-4o"}},
    },
    "edges": [
        {"source": "start", "target": "check"},
        {"source": "check", "target": {"true": "yes_path", "false": "no_path"}},
    ],
    "entry": "start",
}

NEW_TYPES_DSL = {
    "version": "1.0",
    "name": "new-types",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "retrieve": {
            "type": "knowledge-retrieval",
            "config": {"kb_ids": ["kb-1"], "query_template": "{{query}}", "top_k": 3},
        },
        "set_var": {
            "type": "variable-set",
            "config": {"variable_name": "answer", "value": "found"},
        },
    },
    "edges": [{"source": "retrieve", "target": "set_var"}],
    "entry": "retrieve",
}


@pytest.mark.asyncio
async def test_e2e_create_save_retrieve(client: AsyncClient) -> None:
    """Create → update DSL → verify round-trip via GET."""
    # Create
    create_resp = await client.post("/api/workflows", json={"name": "e2e-1", "graph_dsl": SIMPLE_DSL})
    assert create_resp.status_code == 201
    wf = create_resp.json()
    wf_id = wf["id"]
    assert wf["current_version"] == 1

    # Update with new DSL
    update_resp = await client.put(f"/api/workflows/{wf_id}", json={"graph_dsl": LINEAR_DSL})
    assert update_resp.status_code == 200
    assert update_resp.json()["current_version"] == 2

    # Retrieve and verify
    get_resp = await client.get(f"/api/workflows/{wf_id}")
    assert get_resp.status_code == 200
    result = get_resp.json()
    assert result["name"] == "e2e-1"
    assert result["current_version"] == 2

    # Check version history
    versions_resp = await client.get(f"/api/workflows/{wf_id}/versions")
    versions = versions_resp.json()
    assert len(versions) == 2


@pytest.mark.asyncio
async def test_e2e_validate_and_test_run(client: AsyncClient) -> None:
    """Create → validate → test-run → verify node results."""
    # Create with linear graph
    create_resp = await client.post("/api/workflows", json={"name": "e2e-run", "graph_dsl": LINEAR_DSL})
    wf_id = create_resp.json()["id"]

    # Validate
    val_resp = await client.post(
        f"/api/workflows/{wf_id}/validate",
        json={"graph_dsl": LINEAR_DSL},
    )
    assert val_resp.json()["valid"] is True

    # Test run
    run_resp = await client.post(
        f"/api/workflows/{wf_id}/test-run",
        json={
            "input_data": {"messages": [{"role": "user", "content": "hello"}]},
            "mock": True,
        },
    )
    assert run_resp.status_code == 200
    result = run_resp.json()
    assert result["status"] == "completed"

    node_ids = {n["node_id"] for n in result["nodes"]}
    assert "A" in node_ids
    assert "B" in node_ids


@pytest.mark.asyncio
async def test_e2e_conditional_branch(client: AsyncClient) -> None:
    """Test-run a conditional graph — verify start + check execute."""
    create_resp = await client.post("/api/workflows", json={"name": "e2e-cond", "graph_dsl": CONDITION_DSL})
    wf_id = create_resp.json()["id"]

    run_resp = await client.post(
        f"/api/workflows/{wf_id}/test-run",
        json={"input_data": {}, "mock": True},
    )
    assert run_resp.status_code == 200
    result = run_resp.json()
    assert result["status"] == "completed"

    node_ids = {n["node_id"] for n in result["nodes"]}
    assert "start" in node_ids
    assert "check" in node_ids


@pytest.mark.asyncio
async def test_e2e_new_node_types(client: AsyncClient) -> None:
    """Create workflow with knowledge-retrieval + variable-set, validate + test-run."""
    create_resp = await client.post("/api/workflows", json={"name": "e2e-new", "graph_dsl": NEW_TYPES_DSL})
    assert create_resp.status_code == 201
    wf_id = create_resp.json()["id"]

    # Validate
    val_resp = await client.post(
        f"/api/workflows/{wf_id}/validate",
        json={"graph_dsl": NEW_TYPES_DSL},
    )
    assert val_resp.json()["valid"] is True

    # Test run
    run_resp = await client.post(
        f"/api/workflows/{wf_id}/test-run",
        json={"input_data": {}, "mock": True},
    )
    assert run_resp.status_code == 200
    result = run_resp.json()
    assert result["status"] == "completed"

    node_ids = {n["node_id"] for n in result["nodes"]}
    assert "retrieve" in node_ids
    assert "set_var" in node_ids
