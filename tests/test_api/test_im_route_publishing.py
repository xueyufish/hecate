"""Tests for IM publishing-channel routing (1.3.20).

Covers the webhook's channel-resolution semantics:

- No configured im channel → message rejected (200 ``ok:false``), never
  enqueued — there is no default-agent fallback.
- A published-mode channel routes to the agent's latest published
  version, re-resolved per message (repointing needs no restart).
- A pinned-mode channel routes to the locked version regardless of new
  publishes.

Agents/versions/channels are seeded through the studio API (``client``
fixture, which commits), and the webhook is exercised against a minimal
app sharing the same test engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hecate.channel.adapter import ChannelBase
from hecate.channel.api.v1.channels import router as im_channels_router
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.im.message_bus import IMMessageBus
from hecate.channel.types import CanonicalMessage, MessageContent
from hecate.core.deps import get_db
from hecate.core.plugin.manifest import PluginManifest
from hecate.core.plugin.registry import PluginRegistry


class _StubAdapter(ChannelBase):
    def __init__(self, name: str = "feishu") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "stub"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(markdown=True)

    async def receive(self, raw: object) -> CanonicalMessage:
        payload = raw if isinstance(raw, dict) else {}
        return CanonicalMessage(
            id=__import__("uuid").uuid4(),
            channel_id=self.name,
            user_id="ou_abc",
            session_id=None,
            content=MessageContent(text=str(payload.get("text", "hi"))),
            metadata={"chat_id": "oc_chat"},
        )

    async def respond(self, message_id: str, response: object) -> None:
        return None

    async def stream(self, message_id: str, chunks: object) -> None:
        return None


def _webhook_app_with_db(bus: IMMessageBus) -> FastAPI:
    """Minimal app whose get_db resolves to the shared test engine."""
    from tests.conftest import test_session_factory

    app = FastAPI()
    app.include_router(im_channels_router)
    app.state.plugin_registry = PluginRegistry()
    adapter = _StubAdapter(name="feishu")
    app.state.plugin_registry.register(
        PluginManifest(
            type="channel",
            name=adapter.name,
            version="1.0.0",
            api_version="1.0",
            min_platform_version="0.6.0",
            description=adapter.description,
        ),
        adapter,
    )
    app.state.im_message_bus = bus

    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    return app


async def _seed_published_agent(client: AsyncClient, persona: str = "IM persona") -> dict:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "IM Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
            "persona": persona,
        },
    )
    assert resp.status_code == 201
    agent = resp.json()
    commit = await client.post(f"/api/agents/{agent['id']}/versions/commit", json={})
    assert commit.status_code == 200
    publish = await client.post(f"/api/agents/{agent['id']}/publish/1")
    assert publish.status_code == 200
    return agent


async def _spy_bus() -> tuple[IMMessageBus, list]:
    bus = IMMessageBus()
    calls: list = []

    async def _spy(**kwargs: object) -> None:
        calls.append(kwargs)

    bus.enqueue = _spy  # type: ignore[assignment]
    bus.attach_workflow_service(MagicMock())
    return bus, calls


@pytest.mark.asyncio
async def test_no_channel_configured_rejects_message(client: AsyncClient) -> None:
    await _seed_published_agent(client)  # an agent exists, but no channel row
    bus, calls = await _spy_bus()
    app = _webhook_app_with_db(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/v1/channels/feishu/webhook", json={"text": "hi"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "no channel configured"}
    assert calls == []


@pytest.mark.asyncio
async def test_published_channel_routes_latest_publish_per_message(client: AsyncClient) -> None:
    agent = await _seed_published_agent(client, persona="V1")
    agent_id = agent["id"]
    channel = await client.post(
        "/api/channels",
        json={"name": "feishu-main", "type": "im", "agent_id": agent_id, "config": {"provider": "feishu"}},
    )
    assert channel.status_code == 201

    bus, calls = await _spy_bus()
    app = _webhook_app_with_db(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/v1/channels/feishu/webhook", json={"text": "hi"})
        assert resp.json()["ok"] is True
        assert len(calls) == 1
        assert str(calls[0]["agent_id"]) == agent_id
        assert calls[0]["agent_version"] == 1

        # Publish v2: the NEXT message resolves to it (no restart).
        await client.post(f"/api/agents/{agent_id}/versions/commit", json={})
        pub = await client.post(f"/api/agents/{agent_id}/publish/2")
        assert pub.status_code == 200

        resp = await c.post("/v1/channels/feishu/webhook", json={"text": "hi again"})
        assert resp.json()["ok"] is True
        assert len(calls) == 2
        assert calls[1]["agent_version"] == 2


@pytest.mark.asyncio
async def test_pinned_channel_routes_locked_version(client: AsyncClient) -> None:
    agent = await _seed_published_agent(client, persona="V1")
    agent_id = agent["id"]
    channel = await client.post(
        "/api/channels",
        json={
            "name": "feishu-pin",
            "type": "im",
            "agent_id": agent_id,
            "bind_mode": "pinned",
            "pinned_version": 1,
            "config": {"provider": "feishu"},
        },
    )
    assert channel.status_code == 201

    await client.post(f"/api/agents/{agent_id}/versions/commit", json={})
    await client.post(f"/api/agents/{agent_id}/publish/2")

    bus, calls = await _spy_bus()
    app = _webhook_app_with_db(bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/v1/channels/feishu/webhook", json={"text": "hi"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(calls) == 1
    assert calls[0]["agent_version"] == 1


@pytest.mark.asyncio
async def test_bus_process_threads_agent_version_into_execute() -> None:
    """The bus worker forwards the envelope's resolved version to execute()."""
    import uuid as uuid_mod

    from hecate.channel.im.message_bus import _Envelope
    from hecate.channel.types import CanonicalMessage, MessageContent

    bus = IMMessageBus()
    service = MagicMock()
    service.execute = AsyncMock(return_value={"text": "ok"})
    bus.attach_workflow_service(service)

    envelope = _Envelope(
        message=CanonicalMessage(
            id=uuid_mod.uuid4(),
            channel_id="feishu",
            user_id="ou_abc",
            session_id=None,
            content=MessageContent(text="hi"),
            metadata={"chat_id": "oc_chat"},
        ),
        adapter=_StubAdapter(),
        workspace_id=None,
        chat_id="oc_chat",
        capabilities=ChannelCapabilities(markdown=True),
        agent_id=uuid_mod.UUID(int=7),
        agent_version=3,
    )
    await bus._process(envelope, worker_id=0)
    assert service.execute.await_args.kwargs["agent_version"] == 3
    assert str(service.execute.await_args.kwargs["agent_id"]) == str(uuid_mod.UUID(int=7))
