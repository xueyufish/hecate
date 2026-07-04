"""Tests for Budget Management API — CRUD, status, chargeback."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


class TestBudgetAPI:
    async def test_create_budget(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/budgets",
            json={
                "name": "Test Budget",
                "scope": "workspace",
                "scope_id": str(uuid.uuid4()),
                "limit_value": 1000.0,
                "window_type": "monthly",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Budget"
        assert data["resource_type"] == "cost"

    async def test_list_budgets(self, client: AsyncClient) -> None:
        resp = await client.get("/api/budgets")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_get_nonexistent_budget(self, client: AsyncClient) -> None:
        resp = await client.get(f"/api/budgets/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_update_budget(self, client: AsyncClient) -> None:
        create_resp = await client.post(
            "/api/budgets",
            json={
                "name": "Update Test",
                "scope": "workspace",
                "scope_id": str(uuid.uuid4()),
                "limit_value": 500.0,
            },
        )
        budget_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/budgets/{budget_id}",
            json={"limit_value": 1000.0},
        )
        assert resp.status_code == 200
        assert resp.json()["limit_value"] == 1000.0

    async def test_delete_budget(self, client: AsyncClient) -> None:
        create_resp = await client.post(
            "/api/budgets",
            json={
                "name": "Delete Test",
                "scope": "workspace",
                "scope_id": str(uuid.uuid4()),
                "limit_value": 500.0,
            },
        )
        budget_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/budgets/{budget_id}")
        assert resp.status_code == 204

    async def test_get_budget_status(self, client: AsyncClient) -> None:
        create_resp = await client.post(
            "/api/budgets",
            json={
                "name": "Status Test",
                "scope": "workspace",
                "scope_id": str(uuid.uuid4()),
                "limit_value": 2000.0,
            },
        )
        budget_id = create_resp.json()["id"]

        resp = await client.get(f"/api/budgets/{budget_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "budget" in data
        assert "utilization" in data
        assert "forecast" in data
