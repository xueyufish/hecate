"""Tests for evaluation REST API endpoints."""

from __future__ import annotations

import uuid


class TestDatasetEndpoints:
    async def test_create_dataset(self, client: object) -> None:
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "test-dataset", "description": "test"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-dataset"
        assert data["description"] == "test"
        assert "id" in data

    async def test_list_datasets(self, client: object) -> None:
        # Create two datasets
        await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "ds1"},
        )
        await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "ds2"},
        )
        response = await client.get("/api/evaluation/datasets")  # type: ignore[union-attr]
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2

    async def test_get_dataset(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "get-test"},
        )
        ds_id = create_resp.json()["id"]
        response = await client.get(f"/api/evaluation/datasets/{ds_id}")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["name"] == "get-test"

    async def test_get_dataset_not_found(self, client: object) -> None:
        response = await client.get(f"/api/evaluation/datasets/{uuid.uuid4()}")  # type: ignore[union-attr]
        assert response.status_code == 404

    async def test_update_dataset(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "original"},
        )
        ds_id = create_resp.json()["id"]
        response = await client.put(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds_id}",
            json={"name": "renamed"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "renamed"

    async def test_delete_dataset(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "to-delete"},
        )
        ds_id = create_resp.json()["id"]
        response = await client.delete(f"/api/evaluation/datasets/{ds_id}")  # type: ignore[union-attr]
        assert response.status_code == 204

        # Verify it's gone
        get_resp = await client.get(f"/api/evaluation/datasets/{ds_id}")  # type: ignore[union-attr]
        assert get_resp.status_code == 404


class TestItemEndpoints:
    async def test_add_items(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "items-test"},
        )
        ds_id = create_resp.json()["id"]

        response = await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds_id}/items",
            json=[
                {"query": "What is Python?"},
                {"query": "What is Java?", "expected_answer": "A language"},
            ],
        )
        assert response.status_code == 201
        assert response.json()["added"] == 2

    async def test_list_items(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "list-items-test"},
        )
        ds_id = create_resp.json()["id"]

        await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds_id}/items",
            json=[{"query": "q1"}, {"query": "q2"}],
        )
        response = await client.get(f"/api/evaluation/datasets/{ds_id}/items")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["total"] == 2

    async def test_delete_item(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "del-item-test"},
        )
        ds_id = create_resp.json()["id"]

        await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds_id}/items",
            json=[{"query": "to-delete"}],
        )
        items_resp = await client.get(f"/api/evaluation/datasets/{ds_id}/items")  # type: ignore[union-attr]
        item_id = items_resp.json()["items"][0]["id"]

        response = await client.delete(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds_id}/items/{item_id}",
        )
        assert response.status_code == 204


class TestRunEndpoints:
    async def test_list_runs_empty(self, client: object) -> None:
        response = await client.get("/api/evaluation/runs")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_create_run_invalid_evaluator(self, client: object) -> None:
        create_resp = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets",
            json={"name": "run-invalid"},
        )
        ds_id = create_resp.json()["id"]

        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/runs",
            json={"dataset_id": ds_id, "evaluators": ["nonexistent"]},
        )
        assert response.status_code == 422

    async def test_create_run_invalid_dataset(self, client: object) -> None:
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/runs",
            json={"dataset_id": str(uuid.uuid4()), "evaluators": ["relevancy"]},
        )
        assert response.status_code == 404
