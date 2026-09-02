"""Tests for the hecate-channel-feishu plugin package (PR5b).

Adapter instantiation is gated on the optional ``lark_oapi`` dependency;
the receive/respond/stream logic is tested without the real SDK by
constructing the object via ``__new__`` and stubbing the underlying SDK
channel. ``verify_webhook`` delegates to the SDK's
``handle_webhook_request`` and is tested with a recording stub.
"""

from __future__ import annotations

import pytest
from hecate_channel_feishu.channel import FeishuChannel


class _RecordingUnderlying:
    """Stub ``lark_oapi`` channel recording verify/send calls."""

    def __init__(self, verify_result: tuple[int, dict] = (200, {})) -> None:
        self.verify_calls: list[tuple[dict[str, str], bytes]] = []
        self.send_calls: list[tuple[str, dict]] = []
        self._verify_result = verify_result

    async def handle_webhook_request(self, headers: dict[str, str], body: bytes) -> tuple[int, dict]:
        self.verify_calls.append((headers, body))
        return self._verify_result

    async def send(self, chat_id: str, payload: dict) -> None:
        self.send_calls.append((chat_id, payload))


def _make(underlying: _RecordingUnderlying | None = None) -> FeishuChannel:
    adapter = FeishuChannel.__new__(FeishuChannel)
    adapter._app_id = "cli_xxx"  # type: ignore[attr-defined]
    adapter._app_secret = "***"  # type: ignore[attr-defined]
    adapter._encrypt_key = None  # type: ignore[attr-defined]
    adapter._verification_token = None  # type: ignore[attr-defined]
    adapter._transport = "webhook"  # type: ignore[attr-defined]
    adapter._underlying = underlying  # type: ignore[attr-defined]
    return adapter


def test_capabilities_shape() -> None:
    """Feishu reports streaming, markdown, rich_cards, file_upload."""
    adapter = _make()
    caps = adapter.capabilities
    assert caps.streaming is True
    assert caps.markdown is True
    assert caps.rich_cards is True
    assert caps.file_upload is True
    assert caps.max_message_length == 30000


@pytest.mark.asyncio
async def test_receive_normalizes_event() -> None:
    adapter = _make(underlying=None)
    raw = {
        "message_id": "om_abc",
        "sender": {"sender_id": "ou_user"},
        "chat_id": "oc_chat",
        "chat_type": "p2p",
        "content_text": "hello",
        "create_time": 1700000000,
        "mentioned_bot": True,
    }
    canonical = await adapter.receive(raw)
    assert canonical.channel_id == "feishu"
    assert canonical.user_id == "ou_user"
    assert canonical.content.text == "hello"
    assert canonical.metadata["chat_id"] == "oc_chat"
    assert canonical.metadata["chat_type"] == "p2p"


@pytest.mark.asyncio
async def test_respond_dispatches_to_underlying() -> None:
    underlying = _RecordingUnderlying()
    adapter = _make(underlying=underlying)

    await adapter.respond("om_1", {"chat_id": "oc_chat", "text": "echo"})
    assert underlying.send_calls == [("oc_chat", {"text": "echo"})]


@pytest.mark.asyncio
async def test_respond_requires_chat_id() -> None:
    adapter = _make(underlying=None)
    with pytest.raises(ValueError):
        await adapter.respond("om_1", {"text": "hi"})


@pytest.mark.asyncio
async def test_stream_collects_and_dispatches() -> None:
    underlying = _RecordingUnderlying()
    adapter = _make(underlying=underlying)

    async def _gen():
        yield {"chat_id": "oc_1"}
        yield {"text": "Hello "}
        yield {"text": "world"}

    await adapter.stream("om_1", _gen())
    assert underlying.send_calls == [("oc_1", {"text": "Hello world"})]


@pytest.mark.asyncio
async def test_verify_webhook_delegates_to_sdk_handler() -> None:
    underlying = _RecordingUnderlying()
    adapter = _make(underlying=underlying)

    headers = {"x-lark-request-id": "req_1"}
    result = await adapter.verify_webhook(headers, b'{"type":"url_verification"}')

    assert result == (200, {})
    assert underlying.verify_calls == [(headers, b'{"type":"url_verification"}')]


@pytest.mark.asyncio
async def test_verify_webhook_passes_sdk_rejection_through() -> None:
    underlying = _RecordingUnderlying(verify_result=(403, {"error": "invalid signature"}))
    adapter = _make(underlying=underlying)

    status, payload = await adapter.verify_webhook({}, b"{}")
    assert status == 403
    assert payload == {"error": "invalid signature"}


@pytest.mark.asyncio
async def test_verify_webhook_without_underlying_passes_through() -> None:
    adapter = _make(underlying=None)
    assert await adapter.verify_webhook({}, b"{}") == (200, {})
