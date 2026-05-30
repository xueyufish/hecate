"""Tests for workflow test run history endpoint (GET /api/workflows/{id}/runs)."""

from __future__ import annotations

from httpx import AsyncClient

VALID_DSL = {
    "version": "1.0",
    "name": "test",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}


async def _create_workflow(client: AsyncClient) -> str:
    """Helper to create a workflow and return its ID."""
    data = {"name": "test-runs-workflow", "graph_dsl": VALID_DSL}
    response = await client.post("/api/workflows", json=data)
    assert response.status_code == 201
    return response.json()["id"]


async def _run_test(client: AsyncClient, workflow_id: str) -> dict:
    """Helper to trigger a test run and return the result."""
    response = await client.post(
        f"/api/workflows/{workflow_id}/test-run",
        json={"input_data": {}, "mock": True},
    )
    assert response.status_code == 200
    return response.json()


async def test_list_runs_empty(client: AsyncClient) -> None:
    """Test GET /runs returns empty list for workflow with no runs."""
    workflow_id = await _create_workflow(client)

    response = await client.get(f"/api/workflows/{workflow_id}/runs")
    assert response.status_code == 200

    result = response.json()
    assert result["items"] == []
    assert result["total"] == 0


async def test_list_runs_after_test_run(client: AsyncClient) -> None:
    """Test GET /runs returns run history after executing a test run."""
    workflow_id = await _create_workflow(client)
    run_result = await _run_test(client, workflow_id)

    response = await client.get(f"/api/workflows/{workflow_id}/runs")
    assert response.status_code == 200

    result = response.json()
    assert result["total"] == 1
    assert len(result["items"]) == 1

    run = result["items"][0]
    assert run["run_id"] == run_result["run_id"]
    assert run["status"] == "completed"
    assert run["mock"] is True
    assert run["workflow_id"] == workflow_id
    assert "node_results" in run
    assert "total_duration_ms" in run


async def test_list_runs_multiple(client: AsyncClient) -> None:
    """Test GET /runs returns multiple runs in reverse chronological order."""
    workflow_id = await _create_workflow(client)
    await _run_test(client, workflow_id)
    await _run_test(client, workflow_id)

    response = await client.get(f"/api/workflows/{workflow_id}/runs")
    assert response.status_code == 200

    result = response.json()
    assert result["total"] == 2
    assert len(result["items"]) == 2

    # Most recent first
    assert result["items"][0]["created_at"] >= result["items"][1]["created_at"]


async def test_list_runs_pagination(client: AsyncClient) -> None:
    """Test GET /runs supports pagination."""
    workflow_id = await _create_workflow(client)
    for _ in range(5):
        await _run_test(client, workflow_id)

    response = await client.get(
        f"/api/workflows/{workflow_id}/runs",
        params={"page": 1, "page_size": 2},
    )
    assert response.status_code == 200

    result = response.json()
    assert result["total"] == 5
    assert len(result["items"]) == 2


async def test_list_runs_isolated_per_workflow(client: AsyncClient) -> None:
    """Test GET /runs only returns runs for the specified workflow."""
    wf1_id = await _create_workflow(client)
    wf2_id = await _create_workflow(client)
    await _run_test(client, wf1_id)
    await _run_test(client, wf1_id)
    await _run_test(client, wf2_id)

    response1 = await client.get(f"/api/workflows/{wf1_id}/runs")
    response2 = await client.get(f"/api/workflows/{wf2_id}/runs")

    assert response1.json()["total"] == 2
    assert response2.json()["total"] == 1
