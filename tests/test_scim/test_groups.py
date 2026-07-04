"""Tests for SCIM Group endpoints."""

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


class TestSCIMGroups:
    async def test_create_group(self, client: AsyncClient, scim_headers: dict) -> None:
        resp = await client.post(
            "/scim/v2/Groups",
            json={"displayName": "Test Group"},
            headers=scim_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["displayName"] == "Test Group"

    async def test_list_groups(self, client: AsyncClient, scim_headers: dict) -> None:
        resp = await client.get("/scim/v2/Groups", headers=scim_headers)
        assert resp.status_code == 200

    async def test_get_nonexistent_group(self, client: AsyncClient, scim_headers: dict) -> None:
        resp = await client.get("/scim/v2/Groups/nonexistent", headers=scim_headers)
        assert resp.status_code == 404
