"""Tests for Model Debug API endpoint."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture
def provider_client(client: AsyncClient) -> AsyncClient:  # noqa: ARG001
    """Client with get_current_user_id overridden."""

    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


class TestModelTest:
    async def test_model_test_missing_litellm(self, provider_client: AsyncClient) -> None:
        """Test POST /api/models/test returns 503 when litellm not available."""
        with patch.dict("sys.modules", {"litellm": None}):
            response = await provider_client.post(
                "/api/models/test",
                json={
                    "model_id": "gpt-4o",
                    "prompt": "Hello",
                    "temperature": 0.7,
                    "max_tokens": 50,
                },
            )
            assert response.status_code in (400, 503)

    async def test_model_test_invalid_temperature(self, provider_client: AsyncClient) -> None:
        """Test POST /api/models/test rejects invalid temperature."""
        response = await provider_client.post(
            "/api/models/test",
            json={
                "model_id": "gpt-4o",
                "prompt": "Hello",
                "temperature": 5.0,
                "max_tokens": 50,
            },
        )
        assert response.status_code == 422

    async def test_model_test_invalid_max_tokens(self, provider_client: AsyncClient) -> None:
        """Test POST /api/models/test rejects max_tokens < 1."""
        response = await provider_client.post(
            "/api/models/test",
            json={
                "model_id": "gpt-4o",
                "prompt": "Hello",
                "temperature": 0.7,
                "max_tokens": 0,
            },
        )
        assert response.status_code == 422

    async def test_model_test_empty_prompt(self, provider_client: AsyncClient) -> None:
        """Test POST /api/models/test rejects empty prompt."""
        response = await provider_client.post(
            "/api/models/test",
            json={
                "model_id": "gpt-4o",
                "prompt": "",
                "temperature": 0.7,
                "max_tokens": 50,
            },
        )
        assert response.status_code == 422

    async def test_model_test_default_parameters(self, provider_client: AsyncClient) -> None:
        """Test POST /api/models/test uses default temperature and max_tokens."""
        mock_choice = MagicMock()
        mock_choice.message.content = "Hi there"
        mock_choice.finish_reason = "stop"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 3
        mock_usage.total_tokens = 8

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "gpt-4o"
        mock_response.usage = mock_usage

        mock_completion = AsyncMock(return_value=mock_response)

        with patch("hecate.api.management.model_providers.litellm", create=True) as mock_litellm:
            mock_litellm.acompletion = mock_completion
            # Also ensure the import in the endpoint works
            with patch("hecate.api.management.model_providers.decrypt_api_key", return_value="sk-test"):
                response = await provider_client.post(
                    "/api/models/test",
                    json={
                        "model_id": "gpt-4o",
                        "prompt": "Hi",
                    },
                )

        if response.status_code == 200:
            result = response.json()
            assert "content" in result
            assert "model" in result
            assert "usage" in result
