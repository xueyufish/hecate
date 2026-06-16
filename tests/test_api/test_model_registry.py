"""Tests for Model Registry API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


def _provider_payload(display_name: str = "Test Provider") -> dict:
    return {
        "display_name": display_name,
        "api_key": "sk-test-key-123",
    }


@pytest.fixture
def provider_client(client: AsyncClient) -> AsyncClient:  # noqa: ARG001
    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


async def _create_provider(provider_client: AsyncClient, display_name: str = "Test Provider") -> dict:
    resp = await provider_client.post("/api/model-providers", json=_provider_payload(display_name))
    assert resp.status_code == 201
    return resp.json()


class TestListModels:
    async def test_list_models_empty(self, provider_client: AsyncClient) -> None:
        """Test GET /api/models returns empty when no providers exist."""
        response = await provider_client.get("/api/models")
        assert response.status_code == 200
        result = response.json()
        assert result["items"] == []
        assert result["total"] == 0

    async def test_list_models_with_provider(self, provider_client: AsyncClient) -> None:
        """Test list returns provider group even with no models."""
        await _create_provider(provider_client)

        response = await provider_client.get("/api/models")
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 0


class TestAddCustomModel:
    async def test_add_custom_model(self, provider_client: AsyncClient) -> None:
        """Test POST /api/models adds a custom model."""
        provider = await _create_provider(provider_client)

        response = await provider_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "custom-llama-3",
                "display_name": "Custom Llama 3",
            },
        )
        assert response.status_code == 201

        result = response.json()
        assert result["model_id"] == "custom-llama-3"
        assert result["display_name"] == "Custom Llama 3"
        assert result["is_custom"] is True
        assert result["is_enabled"] is True

    async def test_add_custom_model_provider_not_found(self, provider_client: AsyncClient) -> None:
        """Test adding model to non-existent provider returns 404."""
        response = await provider_client.post(
            "/api/models",
            json={
                "provider_id": str(uuid.uuid4()),
                "model_id": "test",
                "display_name": "Test",
            },
        )
        assert response.status_code == 404

    async def test_add_custom_model_appears_in_list(self, provider_client: AsyncClient) -> None:
        """Test custom model appears in GET /api/models."""
        provider = await _create_provider(provider_client)

        await provider_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "my-model",
                "display_name": "My Model",
            },
        )

        response = await provider_client.get("/api/models")
        result = response.json()
        assert result["total"] == 1
        assert result["items"][0]["models"][0]["model_id"] == "my-model"


class TestUpdateModel:
    async def test_update_model_enable_disable(self, provider_client: AsyncClient) -> None:
        """Test PUT /api/models/{id} toggles is_enabled."""
        provider = await _create_provider(provider_client)
        model_resp = await provider_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "toggle-model",
                "display_name": "Toggle Model",
            },
        )
        model_id = model_resp.json()["id"]

        response = await provider_client.put(
            f"/api/models/{model_id}",
            json={"is_enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["is_enabled"] is False

    async def test_update_model_display_name(self, provider_client: AsyncClient) -> None:
        """Test updating display_name."""
        provider = await _create_provider(provider_client)
        model_resp = await provider_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "rename-model",
                "display_name": "Old Name",
            },
        )
        model_id = model_resp.json()["id"]

        response = await provider_client.put(
            f"/api/models/{model_id}",
            json={"display_name": "New Name"},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "New Name"

    async def test_update_model_not_found(self, provider_client: AsyncClient) -> None:
        """Test updating non-existent model returns 404."""
        response = await provider_client.put(
            f"/api/models/{uuid.uuid4()}",
            json={"is_enabled": False},
        )
        assert response.status_code == 404
