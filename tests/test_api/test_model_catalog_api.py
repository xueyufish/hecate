"""Tests for model catalog API endpoints."""

from __future__ import annotations

from httpx import AsyncClient


class TestModelCatalogAPI:
    async def test_list_catalog(self, client: AsyncClient) -> None:
        resp = await client.get("/api/models/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    async def test_get_nonexistent_model(self, client: AsyncClient) -> None:
        resp = await client.get("/api/models/catalog/nonexistent")
        assert resp.status_code == 404

    async def test_compare_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/models/catalog/compare?model_ids=nonexistent")
        assert resp.status_code == 200
        assert resp.json() == []
