"""Tests for IM channel adapters (Feishu + Slack).

Adapter instantiation is gated on the optional [tools] dependencies.
The receive/respond/stream logic is tested without the real SDK by
monkey-patching the underlying channel on the adapter instance.
"""

from __future__ import annotations

from typing import Any

import pytest

from hecate.channel.adapter import ChannelBase
from hecate.channel.im.feishu import FeishuChannel
from hecate.channel.im.slack import SlackChannel


def _stub_underlying(adapter: ChannelBase, send_calls: list, update_calls: list) -> None:
    """Replace the underlying SDK with a stub recording ``send`` calls."""

    class _FeishuStub:
        async def send(self, chat_id: str, payload: dict) -> None:
            send_calls.append((chat_id, payload))

        async def update(self, message_id: str, payload: dict) -> None:
            update_calls.append((message_id, payload))

    class _SlackStub:
        class _Client:
            async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
                send_calls.append((kwargs.get("channel"), kwargs))
                return {"ok": True}

        client = _Client()

    if isinstance(adapter, FeishuChannel):
        adapter._underlying = _FeishuStub()  # type: ignore[attr-defined]
    elif isinstance(adapter, SlackChannel):
        adapter._app = _SlackStub()  # type: ignore[attr-defined]


def test_feishu_capabilities_shape() -> None:
    """Feishu reports streaming, markdown, rich_cards, file_upload."""
    adapter = FeishuChannel.__new__(FeishuChannel)
    caps = adapter.capabilities
    assert caps.streaming is True
    assert caps.markdown is True
    assert caps.rich_cards is True
    assert caps.file_upload is True
    assert caps.max_message_length == 30000


def test_slack_capabilities_shape() -> None:
    """Slack reports mrkdwn (not markdown) and supports interactive buttons."""
    adapter = SlackChannel.__new__(SlackChannel)
    caps = adapter.capabilities
    assert caps.streaming is True
    assert caps.markdown is False
    assert caps.rich_cards is True
    assert caps.interactive_buttons is True
    assert caps.max_message_length == 40000


@pytest.mark.asyncio
async def test_feishu_receive_normalizes_event() -> None:
    """FeishuChannel.receive extracts the canonical fields from a raw event."""
    # Bypass SDK init by constructing the object directly.
    adapter = FeishuChannel.__new__(FeishuChannel)
    adapter._app_id = "cli_xxx"  # type: ignore[attr-defined]
    adapter._app_secret = "***"  # type: ignore[attr-defined]
    adapter._encrypt_key = None  # type: ignore[attr-defined]
    adapter._verification_token = None  # type: ignore[attr-defined]
    adapter._transport = "webhook"  # type: ignore[attr-defined]
    adapter._underlying = None  # type: ignore[attr-defined]

    raw = {
        "message_id": "om_abc",
        "sender": {"sender_id": "ou_user"},
        "chat_id": "oc_chat",
        "chat_type": "p2p",
        "content_text": "你好",
        "create_time": 1700000000,
        "mentioned_bot": True,
    }
    canonical = await adapter.receive(raw)
    assert canonical.channel_id == "feishu"
    assert canonical.user_id == "ou_user"
    assert canonical.content.text == "你好"
    assert canonical.metadata["chat_id"] == "oc_chat"
    assert canonical.metadata["chat_type"] == "p2p"


@pytest.mark.asyncio
async def test_slack_receive_filters_bot_subtype() -> None:
    adapter = SlackChannel.__new__(SlackChannel)
    adapter._bot_token = "xoxb-test"  # type: ignore[attr-defined]
    adapter._signing_secret = "secret"  # type: ignore[attr-defined]
    adapter._app_token = None  # type: ignore[attr-defined]
    adapter._app = None  # type: ignore[attr-defined]

    # Bot subtype is rejected to avoid bot-loop feedback.
    with pytest.raises(ValueError):
        await adapter.receive({"subtype": "bot_message", "text": "echo"})

    # Normal user message normalizes fine.
    msg = await adapter.receive(
        {
            "user": "U123",
            "channel": "C456",
            "text": "hello",
            "ts": "1700000000.000100",
        }
    )
    assert msg.channel_id == "slack"
    assert msg.user_id == "U123"
    assert msg.metadata["channel_id"] == "C456"


@pytest.mark.asyncio
async def test_feishu_respond_dispatches_to_underlying() -> None:
    adapter = FeishuChannel.__new__(FeishuChannel)
    adapter._app_id = "cli_xxx"  # type: ignore[attr-defined]
    adapter._app_secret = "***"  # type: ignore[attr-defined]
    adapter._encrypt_key = None  # type: ignore[attr-defined]
    adapter._verification_token = None  # type: ignore[attr-defined]
    adapter._transport = "webhook"  # type: ignore[attr-defined]
    sends: list = []
    updates: list = []
    _stub_underlying(adapter, sends, updates)

    await adapter.respond("om_1", {"chat_id": "oc_chat", "text": "echo"})
    assert sends == [("oc_chat", {"text": "echo"})]


@pytest.mark.asyncio
async def test_slack_respond_dispatches_to_underlying() -> None:
    adapter = SlackChannel.__new__(SlackChannel)
    adapter._bot_token = "xoxb-test"  # type: ignore[attr-defined]
    adapter._signing_secret = "secret"  # type: ignore[attr-defined]
    adapter._app_token = None  # type: ignore[attr-defined]
    sends: list = []
    updates: list = []
    _stub_underlying(adapter, sends, updates)

    await adapter.respond("m_1", {"channel_id": "C1", "text": "hi"})
    assert sends and sends[0][0] == "C1"
    assert sends[0][1]["text"] == "hi"
    assert sends[0][1]["channel"] == "C1"


@pytest.mark.asyncio
async def test_slack_respond_requires_channel_id() -> None:
    adapter = SlackChannel.__new__(SlackChannel)
    adapter._bot_token = "xoxb"  # type: ignore[attr-defined]
    adapter._signing_secret = "secret"  # type: ignore[attr-defined]
    adapter._app_token = None  # type: ignore[attr-defined]
    adapter._app = None  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        await adapter.respond("m_1", {"text": "hi"})


@pytest.mark.asyncio
async def test_feishu_respond_requires_chat_id() -> None:
    adapter = FeishuChannel.__new__(FeishuChannel)
    for attr in ("_app_id", "_app_secret"):
        setattr(adapter, attr, "x")
    adapter._encrypt_key = None  # type: ignore[attr-defined]
    adapter._verification_token = None  # type: ignore[attr-defined]
    adapter._transport = "webhook"  # type: ignore[attr-defined]
    adapter._underlying = None  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        await adapter.respond("om_1", {"text": "hi"})


@pytest.mark.asyncio
async def test_feishu_stream_collects_and_dispatches() -> None:
    adapter = FeishuChannel.__new__(FeishuChannel)
    adapter._app_id = "cli_xxx"  # type: ignore[attr-defined]
    adapter._app_secret = "***"  # type: ignore[attr-defined]
    adapter._encrypt_key = None  # type: ignore[attr-defined]
    adapter._verification_token = None  # type: ignore[attr-defined]
    adapter._transport = "webhook"  # type: ignore[attr-defined]
    sends: list = []
    updates: list = []
    _stub_underlying(adapter, sends, updates)

    async def _gen():
        yield {"chat_id": "oc_1"}
        yield {"text": "Hello "}
        yield {"text": "world"}

    await adapter.stream("om_1", _gen())
    assert sends == [("oc_1", {"text": "Hello world"})]


@pytest.mark.asyncio
async def test_slack_stream_collects_and_dispatches() -> None:
    adapter = SlackChannel.__new__(SlackChannel)
    adapter._bot_token = "xoxb"  # type: ignore[attr-defined]
    adapter._signing_secret = "secret"  # type: ignore[attr-defined]
    adapter._app_token = None  # type: ignore[attr-defined]
    sends: list = []
    updates: list = []
    _stub_underlying(adapter, sends, updates)

    async def _gen():
        yield {"channel_id": "C1"}
        yield "echo "
        yield {"text": "this"}

    await adapter.stream("m_1", _gen())
    assert sends and sends[0][0] == "C1"
    assert sends[0][1]["text"] == "echo this"
