"""Tests for agent model availability (fallback integration).

Covers:
- Agent list returns model_available field
- Agent detail returns model_available field
- model_available is True when provider is active and model is enabled
- model_available is False when provider is inactive or model is disabled
- model_available is None when agent has no model configured
- Provider status change affects agent model availability
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


def _provider_payload(**overrides: object) -> dict:
    payload = {
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
    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


async def _create_provider_with_model(
    provider_client: AsyncClient,
    provider_name: str = "test-provider",
    model_id: str = "test-model-1",
    provider_enabled: bool = True,
    provider_status: str = "active",
) -> dict:
    """Helper to create a provider and register a model."""
    from sqlalchemy import select

    from hecate.models.model_provider import ModelProviderModel

    resp = await provider_client.post(
        "/api/model-providers",
        json=_provider_payload(display_name=f"{provider_name} Display"),
    )
    assert resp.status_code == 201
    provider_data = resp.json()
    provider_id = provider_data["id"]

    # Directly register a model via API
    model_resp = await provider_client.post(
        "/api/models",
        json={
            "provider_id": provider_id,
            "model_id": model_id,
            "display_name": f"Test Model {model_id}",
        },
    )
    assert model_resp.status_code == 201

    # Update provider status and enabled state if needed
    if provider_status != "pending" or not provider_enabled:

        async def _override_db():
            from tests.conftest import test_session_factory

            async with test_session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        # We need to directly manipulate DB since there's no API for status
        # Use the provider update endpoint to change enabled
        if not provider_enabled:
            await provider_client.put(
                f"/api/model-providers/{provider_id}",
                json={"is_enabled": False},
            )

        # For status, we need direct DB access
        from tests.conftest import test_session_factory

        async with test_session_factory() as session:
            result = await session.execute(
                select(ModelProviderModel).where(ModelProviderModel.id == uuid.UUID(provider_id))
            )
            provider = result.scalar_one()
            provider.status = provider_status
            await session.commit()

    return {"provider_id": provider_id, "model_id": model_id}


class TestAgentModelAvailability:
    async def test_agent_list_model_available_true(self, provider_client: AsyncClient) -> None:
        """model_available=True when provider is active and model is enabled."""
        await _create_provider_with_model(provider_client, provider_status="active")

        agent_resp = await provider_client.post(
            "/api/agents",
            json={
                "name": "Agent With Model",
                "model_config": {"model": "test-model-1"},
                "mode": "chat",
            },
        )
        assert agent_resp.status_code == 201

        list_resp = await provider_client.get("/api/agents")
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        agent = next(a for a in items if a["name"] == "Agent With Model")
        assert agent["model_available"] is True

    async def test_agent_list_model_available_false_provider_disabled(self, provider_client: AsyncClient) -> None:
        """model_available=False when provider is not active."""
        await _create_provider_with_model(
            provider_client,
            provider_name="disabled-provider",
            model_id="disabled-model",
            provider_status="error",
        )

        agent_resp = await provider_client.post(
            "/api/agents",
            json={
                "name": "Agent Disabled Provider",
                "model_config": {"model": "disabled-model"},
                "mode": "chat",
            },
        )
        assert agent_resp.status_code == 201

        list_resp = await provider_client.get("/api/agents")
        items = list_resp.json()["items"]
        agent = next(a for a in items if a["name"] == "Agent Disabled Provider")
        assert agent["model_available"] is False

    async def test_agent_list_model_available_false_provider_not_enabled(self, provider_client: AsyncClient) -> None:
        """model_available=False when provider is_enabled=False."""
        await _create_provider_with_model(
            provider_client,
            provider_name="inactive-provider",
            model_id="inactive-model",
            provider_enabled=False,
            provider_status="active",
        )

        agent_resp = await provider_client.post(
            "/api/agents",
            json={
                "name": "Agent Inactive Provider",
                "model_config": {"model": "inactive-model"},
                "mode": "chat",
            },
        )
        assert agent_resp.status_code == 201

        list_resp = await provider_client.get("/api/agents")
        items = list_resp.json()["items"]
        agent = next(a for a in items if a["name"] == "Agent Inactive Provider")
        assert agent["model_available"] is False

    async def test_agent_list_model_available_none_no_model(self, provider_client: AsyncClient) -> None:
        """model_available=None when agent has no model configured."""
        agent_resp = await provider_client.post(
            "/api/agents",
            json={
                "name": "Agent No Model",
                "model_config": {},
                "mode": "chat",
            },
        )
        assert agent_resp.status_code == 201

        list_resp = await provider_client.get("/api/agents")
        items = list_resp.json()["items"]
        agent = next(a for a in items if a["name"] == "Agent No Model")
        assert agent["model_available"] is None

    async def test_agent_detail_model_available(self, provider_client: AsyncClient) -> None:
        """Agent detail endpoint returns model_available."""
        await _create_provider_with_model(
            provider_client,
            provider_name="detail-provider",
            model_id="detail-model",
            provider_status="active",
        )

        agent_resp = await provider_client.post(
            "/api/agents",
            json={
                "name": "Detail Agent",
                "model_config": {"model": "detail-model"},
                "mode": "chat",
            },
        )
        agent_id = agent_resp.json()["id"]

        detail_resp = await provider_client.get(f"/api/agents/{agent_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["model_available"] is True

    async def test_provider_status_change_affects_agent(self, provider_client: AsyncClient) -> None:
        """Changing provider status from active to error affects agent availability."""
        info = await _create_provider_with_model(
            provider_client,
            provider_name="status-change-provider",
            model_id="status-model",
            provider_status="active",
        )

        agent_resp = await provider_client.post(
            "/api/agents",
            json={
                "name": "Status Agent",
                "model_config": {"model": "status-model"},
                "mode": "chat",
            },
        )
        assert agent_resp.status_code == 201

        list_resp = await provider_client.get("/api/agents")
        items = list_resp.json()["items"]
        agent = next(a for a in items if a["name"] == "Status Agent")
        assert agent["model_available"] is True

        # Change provider status to error
        from sqlalchemy import select

        from hecate.models.model_provider import ModelProviderModel
        from tests.conftest import test_session_factory

        async with test_session_factory() as session:
            result = await session.execute(
                select(ModelProviderModel).where(ModelProviderModel.id == uuid.UUID(info["provider_id"]))
            )
            provider = result.scalar_one()
            provider.status = "error"
            await session.commit()

        list_resp2 = await provider_client.get("/api/agents")
        items2 = list_resp2.json()["items"]
        agent2 = next(a for a in items2 if a["name"] == "Status Agent")
        assert agent2["model_available"] is False
