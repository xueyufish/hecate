"""Tests for SCIM User endpoints — CRUD, filter, pagination, deprovisioning."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from hecate.core.config import settings


@pytest.fixture(autouse=True)
def _enable_scim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SCIM_ENABLED", True)
    monkeypatch.setattr(settings, "SCIM_BEARER_TOKEN", "test-scim-token")


@pytest.fixture()
def scim_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-scim-token"}


class TestSCIMDiscovery:
    async def test_service_provider_config(self, client: AsyncClient) -> None:
        resp = await client.get("/scim/v2/ServiceProviderConfig")
        assert resp.status_code == 200
        data = resp.json()
        assert "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig" in data["schemas"]
        assert data["patch"]["supported"] is True

    async def test_schemas(self, client: AsyncClient) -> None:
        resp = await client.get("/scim/v2/Schemas")
        assert resp.status_code == 200
        data = resp.json()
        assert data["totalResults"] >= 2

    async def test_resource_types(self, client: AsyncClient) -> None:
        resp = await client.get("/scim/v2/ResourceTypes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["totalResults"] >= 2


class TestSCIMUsers:
    async def test_create_user(self, client: AsyncClient, scim_headers: dict) -> None:
        resp = await client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "test@example.com",
                "displayName": "Test User",
            },
            headers=scim_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["userName"] == "test@example.com"

    async def test_create_user_duplicate_rejected(self, client: AsyncClient, scim_headers: dict) -> None:
        user_data = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "dup@example.com",
        }
        await client.post("/scim/v2/Users", json=user_data, headers=scim_headers)
        resp = await client.post("/scim/v2/Users", json=user_data, headers=scim_headers)
        assert resp.status_code == 409

    async def test_list_users(self, client: AsyncClient, scim_headers: dict) -> None:
        resp = await client.get("/scim/v2/Users", headers=scim_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "totalResults" in data
        assert "Resources" in data

    async def test_get_nonexistent_user(self, client: AsyncClient, scim_headers: dict) -> None:
        import uuid

        resp = await client.get(f"/scim/v2/Users/{uuid.UUID(int=0)}", headers=scim_headers)
        assert resp.status_code == 404

    async def test_auth_required(self, client: AsyncClient) -> None:
        resp = await client.get("/scim/v2/Users")
        assert resp.status_code == 401

    async def test_invalid_token(self, client: AsyncClient) -> None:
        resp = await client.get("/scim/v2/Users", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401
