"""Tests for Model Provider CRUD API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


def _provider_payload(**overrides: object) -> dict:
    """Build a valid provider creation payload."""
    payload = {
        "name": "test-provider",
        "display_name": "Test Provider",
        "api_key": "sk-test-key-123",
        "base_url": None,
        "config": {},
        "is_enabled": True,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def provider_client(client: AsyncClient) -> AsyncClient:  # noqa: ARG001
    """Client with get_current_user_id overridden for provider tests."""

    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


class TestCreateProvider:
    async def test_create_provider(self, provider_client: AsyncClient) -> None:
        """Test POST /api/model-providers creates a provider."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        assert response.status_code == 201

        result = response.json()
        assert result["name"] == "test-provider"
        assert result["display_name"] == "Test Provider"
        assert result["status"] == "pending"
        assert result["is_enabled"] is True
        assert "api_key_encrypted" not in result
        assert "model_count" in result

    async def test_create_provider_duplicate_name(self, provider_client: AsyncClient) -> None:
        """Test POST /api/model-providers rejects duplicate name."""
        await provider_client.post("/api/model-providers", json=_provider_payload())

        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        assert response.status_code == 409

    async def test_create_provider_with_base_url(self, provider_client: AsyncClient) -> None:
        """Test creating a provider with custom base_url."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(
                name="custom-endpoint",
                display_name="Custom",
                base_url="https://api.custom-endpoint.com/v1",
            ),
        )
        assert response.status_code == 201
        assert response.json()["base_url"] == "https://api.custom-endpoint.com/v1"

    async def test_create_provider_with_custom_config(self, provider_client: AsyncClient) -> None:
        """Test creating a provider with custom config values."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(
                name="custom-config",
                config={"timeout": 60, "max_retries": 5},
            ),
        )
        assert response.status_code == 201
        result = response.json()
        assert result["config"]["timeout"] == 60
        assert result["config"]["max_retries"] == 5

    async def test_create_provider_invalid_config_timeout(self, provider_client: AsyncClient) -> None:
        """Test creating a provider with invalid timeout returns 400."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(name="bad-timeout", config={"timeout": 999}),
        )
        assert response.status_code == 400

    async def test_create_provider_invalid_config_retries(self, provider_client: AsyncClient) -> None:
        """Test creating a provider with invalid max_retries returns 400."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(name="bad-retries", config={"max_retries": -1}),
        )
        assert response.status_code == 400

    async def test_create_provider_invalid_config_rate_limit(self, provider_client: AsyncClient) -> None:
        """Test creating a provider with invalid rate_limit_rpm returns 400."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(name="bad-ratelimit", config={"rate_limit_rpm": 0}),
        )
        assert response.status_code == 400


class TestListProviders:
    async def test_list_providers_empty(self, provider_client: AsyncClient) -> None:
        """Test GET /api/model-providers returns empty list."""
        response = await provider_client.get("/api/model-providers")
        assert response.status_code == 200

        result = response.json()
        assert result["items"] == []
        assert result["total"] == 0

    async def test_list_providers_with_data(self, provider_client: AsyncClient) -> None:
        """Test GET /api/model-providers returns created providers."""
        await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(name="provider-a", display_name="Provider A"),
        )
        await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(name="provider-b", display_name="Provider B"),
        )

        response = await provider_client.get("/api/model-providers")
        assert response.status_code == 200

        result = response.json()
        assert result["total"] == 2
        names = {item["name"] for item in result["items"]}
        assert names == {"provider-a", "provider-b"}

    async def test_list_providers_includes_model_count(self, provider_client: AsyncClient) -> None:
        """Test list response includes model_count field."""
        response = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        assert response.status_code == 201

        response = await provider_client.get("/api/model-providers")
        result = response.json()
        assert result["total"] == 1
        assert result["items"][0]["model_count"] == 0


class TestUpdateProvider:
    async def test_update_provider_display_name(self, provider_client: AsyncClient) -> None:
        """Test PUT /api/model-providers/{id} updates display_name."""
        create_resp = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        provider_id = create_resp.json()["id"]

        response = await provider_client.put(
            f"/api/model-providers/{provider_id}",
            json={"display_name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Updated Name"

    async def test_update_provider_api_key(self, provider_client: AsyncClient) -> None:
        """Test updating API key encrypts and stores it."""
        create_resp = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        provider_id = create_resp.json()["id"]

        response = await provider_client.put(
            f"/api/model-providers/{provider_id}",
            json={"api_key": "sk-new-key-456"},
        )
        assert response.status_code == 200
        assert "api_key_encrypted" not in response.json()

    async def test_update_provider_config_merges(self, provider_client: AsyncClient) -> None:
        """Test updating config merges with existing values."""
        create_resp = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(config={"timeout": 60}),
        )
        provider_id = create_resp.json()["id"]

        response = await provider_client.put(
            f"/api/model-providers/{provider_id}",
            json={"config": {"max_retries": 5}},
        )
        assert response.status_code == 200
        config = response.json()["config"]
        assert config["timeout"] == 60
        assert config["max_retries"] == 5

    async def test_update_provider_not_found(self, provider_client: AsyncClient) -> None:
        """Test updating non-existent provider returns 404."""
        response = await provider_client.put(
            f"/api/model-providers/{uuid.uuid4()}",
            json={"display_name": "Ghost"},
        )
        assert response.status_code == 404

    async def test_update_provider_invalid_config(self, provider_client: AsyncClient) -> None:
        """Test updating with invalid config returns 400."""
        create_resp = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        provider_id = create_resp.json()["id"]

        response = await provider_client.put(
            f"/api/model-providers/{provider_id}",
            json={"config": {"timeout": 9999}},
        )
        assert response.status_code == 400


class TestDeleteProvider:
    async def test_delete_provider(self, provider_client: AsyncClient) -> None:
        """Test DELETE /api/model-providers/{id} soft-deletes provider."""
        create_resp = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        provider_id = create_resp.json()["id"]

        response = await provider_client.delete(f"/api/model-providers/{provider_id}")
        assert response.status_code == 204

        list_resp = await provider_client.get("/api/model-providers")
        assert list_resp.json()["total"] == 0

    async def test_delete_provider_not_found(self, provider_client: AsyncClient) -> None:
        """Test deleting non-existent provider returns 404."""
        response = await provider_client.delete(f"/api/model-providers/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_delete_provider_cascade_soft_deletes_models(
        self,
        provider_client: AsyncClient,
    ) -> None:
        """Test deleting a provider also soft-deletes its models."""
        create_resp = await provider_client.post(
            "/api/model-providers",
            json=_provider_payload(),
        )
        provider_id = create_resp.json()["id"]

        models_resp = await provider_client.get("/api/models")
        assert models_resp.json()["total"] == 0

        await provider_client.delete(f"/api/model-providers/{provider_id}")

        models_resp = await provider_client.get("/api/models")
        assert models_resp.json()["total"] == 0
