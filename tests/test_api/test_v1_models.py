"""Tests for /v1/models endpoint (OpenAI-compatible).

Tests the priority logic: database providers first, then LiteLLM fallback.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture
def models_client(client: AsyncClient) -> AsyncClient:  # noqa: ARG001
    """Client with get_current_user_id overridden for /v1/models."""

    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


class TestListModels:
    async def test_list_models_no_providers_fallback(
        self,
        models_client: AsyncClient,
    ) -> None:
        """Test GET /v1/models returns LiteLLM fallback when no providers."""
        response = await models_client.get("/v1/models")
        assert response.status_code == 200

        result = response.json()
        assert result["object"] == "list"
        assert "data" in result

    async def test_list_models_with_provider_returns_db_models(
        self,
        models_client: AsyncClient,
    ) -> None:
        """Test GET /v1/models returns DB models when provider exists."""
        provider_resp = await models_client.post(
            "/api/model-providers",
            json={
                "display_name": "OpenAI",
                "api_key": "sk-test-key",
                "is_enabled": True,
            },
        )
        assert provider_resp.status_code == 201
        provider = provider_resp.json()

        model_resp = await models_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "gpt-4o-test",
                "display_name": "GPT-4o Test",
            },
        )
        assert model_resp.status_code == 201

        response = await models_client.get("/v1/models")
        assert response.status_code == 200

        result = response.json()
        assert result["object"] == "list"
        model_ids = [m["id"] for m in result["data"]]
        assert "gpt-4o-test" in model_ids

    async def test_list_models_only_returns_enabled(
        self,
        models_client: AsyncClient,
    ) -> None:
        """Test disabled models are excluded from /v1/models."""
        provider_resp = await models_client.post(
            "/api/model-providers",
            json={
                "display_name": "Filter Test",
                "api_key": "sk-test",
                "is_enabled": True,
            },
        )
        provider = provider_resp.json()

        await models_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "enabled-model",
                "display_name": "Enabled",
            },
        )
        disabled_resp = await models_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "disabled-model",
                "display_name": "Disabled",
            },
        )

        await models_client.put(
            f"/api/models/{disabled_resp.json()['id']}",
            json={"is_enabled": False},
        )

        response = await models_client.get("/v1/models")
        result = response.json()
        model_ids = [m["id"] for m in result["data"]]
        assert "enabled-model" in model_ids
        assert "disabled-model" not in model_ids

    async def test_list_models_includes_provider_info(
        self,
        models_client: AsyncClient,
    ) -> None:
        """Test model objects include provider and provider_display_name."""
        provider_resp = await models_client.post(
            "/api/model-providers",
            json={
                "display_name": "Zhipu AI",
                "api_key": "sk-test",
            },
        )
        provider = provider_resp.json()

        await models_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "glm-4",
                "display_name": "GLM-4",
            },
        )

        response = await models_client.get("/v1/models")
        result = response.json()

        glm_model = next(m for m in result["data"] if m["id"] == "glm-4")
        assert glm_model["provider"]  # auto-generated slug
        assert glm_model["provider_display_name"] == "Zhipu AI"

    async def test_list_models_disabled_provider_excluded(
        self,
        models_client: AsyncClient,
    ) -> None:
        """Test models from disabled provider are excluded."""
        provider_resp = await models_client.post(
            "/api/model-providers",
            json={
                "display_name": "Disabled Provider",
                "api_key": "sk-test",
                "is_enabled": True,
            },
        )
        provider = provider_resp.json()

        await models_client.post(
            "/api/models",
            json={
                "provider_id": provider["id"],
                "model_id": "should-not-show",
                "display_name": "Hidden",
            },
        )

        await models_client.put(
            f"/api/model-providers/{provider['id']}",
            json={"is_enabled": False},
        )

        response = await models_client.get("/v1/models")
        result = response.json()
        model_ids = [m["id"] for m in result["data"]]
        assert "should-not-show" not in model_ids
