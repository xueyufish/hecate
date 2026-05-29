"""End-to-end tests for Model Provider Management flow.

Covers:
- Create Provider → auto-discover models → models appear in /v1/models
- Create Agent with grouped model → chat succeeds with provider config
- Provider config (timeout/retry) is passed to LLM calls
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture
def e2e_client(client: AsyncClient) -> AsyncClient:  # noqa: ARG001
    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


async def _seed_provider_and_model(
    e2e_client: AsyncClient,
    provider_name: str = "e2e-provider",
    model_id: str = "e2e-model",
) -> dict:
    """Create a provider with active status and register a model."""
    from sqlalchemy import select

    from hecate.models.model_provider import ModelProviderModel
    from tests.conftest import test_session_factory

    resp = await e2e_client.post(
        "/api/model-providers",
        json={
            "name": provider_name,
            "display_name": f"{provider_name} Display",
            "api_key": "sk-test-key",
            "config": {"timeout": 60, "max_retries": 5},
        },
    )
    assert resp.status_code == 201
    provider_id = resp.json()["id"]

    # Register model
    model_resp = await e2e_client.post(
        "/api/models",
        json={
            "provider_id": provider_id,
            "model_id": model_id,
            "display_name": f"Test {model_id}",
        },
    )
    assert model_resp.status_code == 201

    # Set provider status to active
    async with test_session_factory() as session:
        result = await session.execute(
            select(ModelProviderModel).where(ModelProviderModel.id == uuid.UUID(provider_id))
        )
        provider = result.scalar_one()
        provider.status = "active"
        await session.commit()

    return {"provider_id": provider_id, "model_id": model_id}


class TestE2EProviderModelFlow:
    async def test_provider_models_appear_in_v1_models(self, e2e_client: AsyncClient) -> None:
        """Models from registered providers appear in /v1/models."""
        await _seed_provider_and_model(e2e_client, provider_name="v1-provider", model_id="v1-model-x")

        resp = await e2e_client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        model_ids = [m["id"] for m in data]
        assert "v1-model-x" in model_ids

        model_obj = next(m for m in data if m["id"] == "v1-model-x")
        assert model_obj["provider"] == "v1-provider"
        assert model_obj["provider_display_name"] == "v1-provider Display"

    async def test_disabled_model_hidden_from_v1_models(self, e2e_client: AsyncClient) -> None:
        """Disabled models are excluded from /v1/models."""
        await _seed_provider_and_model(e2e_client, provider_name="hidden-provider", model_id="hidden-model")

        # Disable the model
        models_resp = await e2e_client.get("/api/models")
        all_models = []
        for group in models_resp.json()["items"]:
            all_models.extend(group["models"])
        target = next(m for m in all_models if m["model_id"] == "hidden-model")
        await e2e_client.put(
            f"/api/models/{target['id']}",
            json={"is_enabled": False},
        )

        resp = await e2e_client.get("/v1/models")
        data = resp.json()["data"]
        model_ids = [m["id"] for m in data]
        assert "hidden-model" not in model_ids

    async def test_chat_with_provider_config(self, e2e_client: AsyncClient) -> None:
        """Chat endpoint passes provider timeout/retry config to LLM service."""
        await _seed_provider_and_model(
            e2e_client,
            provider_name="chat-provider",
            model_id="chat-model",
        )

        mock_response = AsyncMock()
        mock_response.content = "Test response"
        mock_response.tool_calls = None
        mock_response.model = "chat-model"
        mock_response.usage = {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        }
        mock_response.finish_reason = "stop"

        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            resp = await e2e_client.post(
                "/v1/chat/completions",
                json={
                    "model": "chat-model",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
            )
            assert resp.status_code == 200
            assert resp.json()["object"] == "chat.completion"

            mock_llm.chat.assert_called_once()
            call_kwargs = mock_llm.chat.call_args.kwargs
            assert call_kwargs["timeout"] == 60
            assert call_kwargs["num_retries"] == 5

    async def test_chat_without_provider_config(self, e2e_client: AsyncClient) -> None:
        """Chat with unregistered model uses no provider config."""
        mock_response = AsyncMock()
        mock_response.content = "Fallback response"
        mock_response.tool_calls = None
        mock_response.model = "unknown-model"
        mock_response.usage = {
            "prompt_tokens": 2,
            "completion_tokens": 2,
            "total_tokens": 4,
        }
        mock_response.finish_reason = "stop"

        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            resp = await e2e_client.post(
                "/v1/chat/completions",
                json={
                    "model": "unknown-model",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
            assert resp.status_code == 200

            call_kwargs = mock_llm.chat.call_args.kwargs
            assert call_kwargs["timeout"] is None
            assert call_kwargs["num_retries"] is None

    async def test_create_agent_select_model_chat_succeeds(self, e2e_client: AsyncClient) -> None:
        """Create agent with provider model, verify model_available=True, chat works."""
        await _seed_provider_and_model(
            e2e_client,
            provider_name="agent-chat-provider",
            model_id="agent-chat-model",
        )

        # Create agent
        agent_resp = await e2e_client.post(
            "/api/agents",
            json={
                "name": "E2E Chat Agent",
                "model_config": {"model": "agent-chat-model"},
                "mode": "chat",
            },
        )
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["id"]

        # Verify model_available in list
        list_resp = await e2e_client.get("/api/agents")
        agent = next(a for a in list_resp.json()["items"] if a["id"] == agent_id)
        assert agent["model_available"] is True

        # Verify model_available in detail
        detail_resp = await e2e_client.get(f"/api/agents/{agent_id}")
        assert detail_resp.json()["model_available"] is True

        # Chat with the agent's model
        mock_response = AsyncMock()
        mock_response.content = "Agent response"
        mock_response.tool_calls = None
        mock_response.model = "agent-chat-model"
        mock_response.usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        mock_response.finish_reason = "stop"

        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            chat_resp = await e2e_client.post(
                "/v1/chat/completions",
                json={
                    "model": "agent-chat-model",
                    "messages": [{"role": "user", "content": "Test"}],
                },
            )
            assert chat_resp.status_code == 200
