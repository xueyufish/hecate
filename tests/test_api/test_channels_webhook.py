"""Tests for the IM channel webhook router.

Covers the dispatch logic of
``POST /v1/channels/{name}/webhook`` and
``GET /v1/channels/{name}/webhook``:

- URL-verification challenge echoed back without enqueueing.
- Unknown channel name returns 404.
- Missing or missing registry returns 503.
- Signed payload from a registered adapter is normalized and enqueued.

The Slack / Feishu SDKs are mocked so signature verification does not
require real signing secrets.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hecate.api.v1.channels import router as im_channels_router
from hecate.channel.adapter import ChannelABC
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.im.message_bus import IMMessageBus
from hecate.channel.types import CanonicalMessage, MessageContent
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry


class _StubAdapter(ChannelABC):
    """Adapter stub that records receive/respond/stream invocations."""

    def __init__(self, name: str = "stub") -> None:
        self._name = name
        self.received: list[object] = []
        self.responded: list[tuple[str, object]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "stub"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(markdown=True)

    @property
    def underlying(self) -> Any:  # pragma: no cover - unused in tests
        return None

    async def receive(self, raw: object) -> CanonicalMessage:
        self.received.append(raw)
        if isinstance(raw, dict) and raw.get("type") == "url_verification":
            return CanonicalMessage(
                id=__import__("uuid").uuid4(),
                channel_id=self.name,
                user_id="challenge",
                session_id=None,
                content=MessageContent(text=None),
                metadata={"chat_id": "", "challenge": raw.get("challenge", "")},
            )
        return CanonicalMessage(
            id=__import__("uuid").uuid4(),
            channel_id=self.name,
            user_id="ou_abc",
            session_id=None,
            content=MessageContent(text=str(raw.get("text", "hi"))),
            metadata={"chat_id": "oc_chat"},
        )

    async def respond(self, message_id: str, response: object) -> None:
        self.responded.append((message_id, response))

    async def stream(self, message_id: str, chunks: object) -> None:
        self.responded.append((message_id, "stream"))


def _make_app(registry: PluginRegistry | None = None, bus: IMMessageBus | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(im_channels_router)
    app.state.plugin_registry = registry
    app.state.im_message_bus = bus
    return app


@pytest.mark.asyncio
async def test_get_webhook_returns_ok() -> None:
    """GET /webhook is the URL-verification probe — must return 200."""
    app = _make_app(registry=None, bus=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/v1/channels/feishu/webhook")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_unknown_channel_returns_404() -> None:
    """No adapter registered for that name -> 404."""
    registry = PluginRegistry()
    app = _make_app(registry=registry, bus=IMMessageBus())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/nonexistent/webhook",
            json={"type": "message", "text": "hi"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_missing_registry_returns_503() -> None:
    """Registry not initialized (e.g., lifespan crashed) -> 503."""
    app = _make_app(registry=None, bus=IMMessageBus())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/feishu/webhook",
            json={"type": "message", "text": "hi"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_missing_message_bus_returns_503() -> None:
    """Bus not started -> 503, even if registry has the adapter."""
    adapter = _StubAdapter(name="feishu")
    registry = PluginRegistry()
    registry.register(
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
    app = _make_app(registry=registry, bus=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/feishu/webhook",
            json={"type": "message", "text": "hi"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_url_verification_challenge_is_echoed() -> None:
    """Feishu and Slack URL-verification requests are echoed back without
    enqueuing anything into the MessageBus."""
    adapter = _StubAdapter(name="feishu")
    registry = PluginRegistry()
    registry.register(
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
    bus = IMMessageBus()
    app = _make_app(registry=registry, bus=bus)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/feishu/webhook",
            json={"type": "url_verification", "challenge": "abc123"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}
    # Adapter must not have received a CanonicalMessage for the challenge.
    assert adapter.received == []
    # MessageBus queue must be empty.
    assert bus._queue is None or bus._queue.qsize() == 0


@pytest.mark.asyncio
async def test_signed_payload_normalized_and_enqueued() -> None:
    """A normal message payload is normalized via adapter.receive() and
    enqueued into the MessageBus, returning 200."""
    adapter = _StubAdapter(name="feishu")
    registry = PluginRegistry()
    registry.register(
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
    # We patch IMMessageBus.enqueue to assert behavior without running workers.
    bus = IMMessageBus()
    enqueue_calls: list[tuple[object, object, object, str, object]] = []
    bus.attach_workflow_service(MagicMock())
    original_enqueue = bus.enqueue

    async def _spy(  # type: ignore[no-untyped-def]
        canonical_message,
        adapter=None,
        workspace_id=None,
        chat_id="",
        channel_capabilities=None,
        agent_id=None,
    ):
        enqueue_calls.append((canonical_message, adapter, workspace_id, chat_id, channel_capabilities))
        # Don't actually queue to avoid running the consumer loop in tests.

    bus.enqueue = _spy  # type: ignore[assignment]
    try:
        app = _make_app(registry=registry, bus=bus)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/v1/channels/feishu/webhook",
                json={"text": "hi", "chat_id": "oc_chat"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert len(adapter.received) == 1
        assert len(enqueue_calls) == 1
        canonical, captured_adapter, workspace_id, chat_id, caps = enqueue_calls[0]
        assert captured_adapter is adapter
        assert canonical.channel_id == "feishu"
        assert canonical.user_id == "ou_abc"
        assert canonical.content.text == "hi"
        assert chat_id == "oc_chat"
        assert caps.markdown is True
    finally:
        bus.enqueue = original_enqueue  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_invalid_json_returns_400() -> None:
    """Malformed JSON body is rejected by the body decoder."""
    adapter = _StubAdapter(name="feishu")
    registry = PluginRegistry()
    registry.register(
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
    app = _make_app(registry=registry, bus=IMMessageBus())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/feishu/webhook",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
