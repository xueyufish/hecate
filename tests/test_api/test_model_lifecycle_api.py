"""Tests for model lifecycle API endpoints."""

from __future__ import annotations

from httpx import AsyncClient


class TestModelLifecycleAPI:
    async def test_list_deployments(self, client: AsyncClient) -> None:
        resp = await client.get("/api/models/deployments")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_promote_missing_fields(self, client: AsyncClient) -> None:
        resp = await client.post("/api/models/gpt-4o/promote", json={})
        assert resp.status_code == 400

    async def test_deprecate_missing_sunset(self, client: AsyncClient) -> None:
        resp = await client.post("/api/models/gpt-4o/deprecate", json={})
        assert resp.status_code == 400

    async def test_rollback_missing_version(self, client: AsyncClient) -> None:
        resp = await client.post("/api/models/gpt-4o/rollback", json={})
        assert resp.status_code == 400
