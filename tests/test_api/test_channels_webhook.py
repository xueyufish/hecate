"""Tests for the IM channel webhook router.

Covers the dispatch logic of
``POST /v1/channels/{name}/webhook`` and
``GET /v1/channels/{name}/webhook``:

- URL-verification challenge echoed back without enqueueing.
- Unknown channel name returns 404.
- Missing or missing registry returns 503.
- Signed payload from a registered adapter is normalized and enqueued.
- ``verify_webhook`` rejections short-circuit the request (PR5b).

Adapters are stubbed at the :class:`ChannelBase` level so signature
verification does not require real signing secrets; the real Slack /
Feishu verification implementations are covered by
``tests/test_channels/``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hecate.api.v1.channels import router as im_channels_router
from hecate.channel.adapter import ChannelBase
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.im.message_bus import IMMessageBus
from hecate.channel.types import CanonicalMessage, MessageContent
from hecate.core.plugin.manifest import PluginManifest
from hecate.core.plugin.registry import PluginRegistry


class _StubAdapter(ChannelBase):
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


# ---------------------------------------------------------------------------
# verify_webhook dispatch (PR5b) — the route calls the adapter hook before
# JSON decoding; non-200 results short-circuit with the adapter's payload.
# ---------------------------------------------------------------------------


class _VerifyingAdapter(_StubAdapter):
    """Adapter stub whose ``verify_webhook`` returns a canned response."""

    def __init__(self, name: str = "stub", result: tuple[int, dict] = (401, {"error": "bad signature"})) -> None:
        super().__init__(name)
        self._result = result
        self.verified: list[tuple[dict[str, str], bytes]] = []

    async def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> tuple[int, dict]:
        self.verified.append((headers, raw_body))
        return self._result


class _ExplodingVerifyAdapter(_StubAdapter):
    """Adapter stub whose ``verify_webhook`` raises."""

    async def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> tuple[int, dict]:
        raise RuntimeError("simulated SDK failure")


def _register(registry: PluginRegistry, adapter: _StubAdapter) -> None:
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


@pytest.mark.asyncio
async def test_verification_rejection_short_circuits() -> None:
    """A 401 from verify_webhook is returned verbatim; receive() never runs."""
    adapter = _VerifyingAdapter(name="feishu", result=(401, {"error": "bad signature"}))
    registry = PluginRegistry()
    _register(registry, adapter)
    app = _make_app(registry=registry, bus=IMMessageBus())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/feishu/webhook",
            json={"type": "message", "text": "hi"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"error": "bad signature"}
    assert adapter.received == []
    # The hook saw the raw body and the request headers.
    assert len(adapter.verified) == 1
    assert adapter.verified[0][1] == b'{"type":"message","text":"hi"}'
    assert "content-type" in adapter.verified[0][0]


@pytest.mark.asyncio
async def test_verification_body_passthrough_for_non_200() -> None:
    """Any non-200 status short-circuits with the adapter's (status, body)."""
    adapter = _VerifyingAdapter(name="slack", result=(403, {"error": "timestamp too old"}))
    registry = PluginRegistry()
    _register(registry, adapter)
    app = _make_app(registry=registry, bus=IMMessageBus())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/slack/webhook",
            json={"type": "message", "text": "hi"},
        )
    assert resp.status_code == 403
    assert resp.json() == {"error": "timestamp too old"}


@pytest.mark.asyncio
async def test_verification_exception_returns_401() -> None:
    """An exploding verify_webhook becomes a 401, never a 500."""
    adapter = _ExplodingVerifyAdapter(name="slack")
    registry = PluginRegistry()
    _register(registry, adapter)
    app = _make_app(registry=registry, bus=IMMessageBus())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/v1/channels/slack/webhook",
            json={"type": "message", "text": "hi"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Signature verification failed"}
    assert adapter.received == []
