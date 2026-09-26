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
    admin_headers: dict[str, str] | None = None,
) -> dict:
    """Create a provider + model via the platform-admin gate.

    ``admin_headers`` must carry a PLATFORM_ADMIN_API_KEYS bearer token —
    provider mutations are platform admin only (model-provider-management
    spec).
    """
    from sqlalchemy import select

    from hecate.models.model_provider import ModelProviderModel
    from tests.conftest import test_session_factory

    resp = await e2e_client.post(
        "/api/model-providers",
        json={
            "display_name": f"{provider_name} Display",
            "api_key": "sk-test-key",
            "config": {"timeout": 60, "max_retries": 5},
        },
        headers=admin_headers,
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
        headers=admin_headers,
    )
    assert model_resp.status_code == 201
    model_uuid = model_resp.json()["id"]

    # Publish the model to the reference surface (6.47): seed the test
    # evidence, then run the explicit publish action.
    from datetime import UTC, datetime

    from hecate.models.model_provider import ModelRegistryModel

    async with test_session_factory() as session:
        result = await session.execute(select(ModelRegistryModel).where(ModelRegistryModel.id == uuid.UUID(model_uuid)))
        registry_model = result.scalar_one()
        registry_model.last_test_passed_at = datetime.now(UTC)
        await session.commit()

    publish_resp = await e2e_client.post(f"/api/models/{model_uuid}/publish", headers=admin_headers)
    assert publish_resp.status_code == 200

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
    async def test_provider_models_appear_in_v1_models(
        self, e2e_client: AsyncClient, platform_admin_token: str
    ) -> None:
        """Models from registered providers appear in /v1/models."""
        await _seed_provider_and_model(
            e2e_client,
            provider_name="v1-provider",
            model_id="v1-model-x",
            admin_headers={"Authorization": f"Bearer {platform_admin_token}"},
        )

        resp = await e2e_client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()["data"]
        model_ids = [m["id"] for m in data]
        assert "v1-model-x" in model_ids

        model_obj = next(m for m in data if m["id"] == "v1-model-x")
        assert model_obj["provider"]  # auto-generated slug
        assert model_obj["provider_display_name"] == "v1-provider Display"

    async def test_disabled_model_hidden_from_v1_models(
        self, e2e_client: AsyncClient, platform_admin_token: str
    ) -> None:
        """Disabled models are excluded from /v1/models."""
        admin_headers = {"Authorization": f"Bearer {platform_admin_token}"}
        await _seed_provider_and_model(
            e2e_client,
            provider_name="hidden-provider",
            model_id="hidden-model",
            admin_headers=admin_headers,
        )

        # Disable the model
        models_resp = await e2e_client.get("/api/models")
        all_models = []
        for group in models_resp.json()["items"]:
            all_models.extend(group["models"])
        target = next(m for m in all_models if m["model_id"] == "hidden-model")
        await e2e_client.put(
            f"/api/models/{target['id']}",
            json={"is_enabled": False},
            headers=admin_headers,
        )

        resp = await e2e_client.get("/v1/models")
        data = resp.json()["data"]
        model_ids = [m["id"] for m in data]
        assert "hidden-model" not in model_ids

    async def test_chat_with_provider_config(self, e2e_client: AsyncClient, platform_admin_token: str) -> None:
        """Chat endpoint passes provider timeout/retry config to LLM service."""
        await _seed_provider_and_model(
            e2e_client,
            provider_name="chat-provider",
            model_id="chat-model",
            admin_headers={"Authorization": f"Bearer {platform_admin_token}"},
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

        with patch("hecate.channel.api.v1.chat.llm_service") as mock_llm:
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

        with patch("hecate.channel.api.v1.chat.llm_service") as mock_llm:
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

    async def test_create_agent_select_model_chat_succeeds(
        self, e2e_client: AsyncClient, platform_admin_token: str
    ) -> None:
        """Create agent with provider model, verify model_available=True, chat works."""
        await _seed_provider_and_model(
            e2e_client,
            provider_name="agent-chat-provider",
            model_id="agent-chat-model",
            admin_headers={"Authorization": f"Bearer {platform_admin_token}"},
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

        with patch("hecate.channel.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=mock_response)
            chat_resp = await e2e_client.post(
                "/v1/chat/completions",
                json={
                    "model": "agent-chat-model",
                    "messages": [{"role": "user", "content": "Test"}],
                },
            )
            assert chat_resp.status_code == 200


# ---------------------------------------------------------------------------
# Authorization matrix (auth-boundary-hardening: model-provider-management)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("setup_database")
class TestModelProviderAuthz:
    async def test_workspace_admin_cannot_mutate_provider(self, client: AsyncClient) -> None:
        """Workspace admin credentials do not cross the platform boundary."""
        resp = await client.post(
            "/api/model-providers",
            json={"display_name": "Nope", "api_key": "sk-nope"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"

    async def test_anonymous_cannot_mutate_provider(self, anonymous_client: AsyncClient) -> None:
        resp = await anonymous_client.post(
            "/api/model-providers",
            json={"display_name": "Nope", "api_key": "sk-nope"},
        )
        assert resp.status_code == 401

    async def test_platform_admin_can_create_provider(self, client: AsyncClient, platform_admin_token: str) -> None:
        resp = await client.post(
            "/api/model-providers",
            json={"display_name": "Admin Provider", "api_key": "sk-admin"},
            headers={"Authorization": f"Bearer {platform_admin_token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["display_name"] == "Admin Provider"
        # The response must not leak provider credentials.
        assert "api_key" not in body
        assert "api_key_encrypted" not in body

    async def test_list_requires_authentication(self, anonymous_client: AsyncClient) -> None:
        resp = await anonymous_client.get("/api/model-providers")
        assert resp.status_code == 401

    async def test_list_allowed_for_authenticated_users(self, client: AsyncClient) -> None:
        resp = await client.get("/api/model-providers")
        assert resp.status_code == 200

    async def test_inactive_user_jwt_rejected_on_mutation(self, anonymous_client: AsyncClient, db_session) -> None:
        """A deactivated user's still-valid JWT is rejected (401)."""

        from hecate.enterprise.auth.password import hash_password
        from hecate.enterprise.auth.token import create_access_token
        from hecate.models.user import UserModel

        suffix = uuid.uuid4().hex[:8]
        user = UserModel(
            email=f"gone-{suffix}@example.com",
            hashed_password=hash_password("deactivated"),
            active=False,
        )
        db_session.add(user)
        await db_session.flush()
        token = create_access_token(user.id)

        resp = await anonymous_client.post(
            "/api/model-providers",
            json={"display_name": "Nope", "api_key": "sk-nope"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
