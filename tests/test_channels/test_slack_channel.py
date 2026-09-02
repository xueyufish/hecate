"""Tests for the hecate-channel-slack plugin package (PR5b).

Adapter instantiation is gated on the optional ``slack_bolt`` dependency;
the receive/respond/stream logic is tested without the real SDK by
constructing the object via ``__new__`` and stubbing the underlying Bolt
app. ``verify_webhook`` is pure stdlib (HMAC + replay window) and is
tested with real signatures.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import pytest
from hecate_channel_slack.channel import SlackChannel


def _make(signing_key: str = "signing-key") -> SlackChannel:
    adapter = SlackChannel.__new__(SlackChannel)
    adapter._bot_token = "xoxb-test"  # type: ignore[attr-defined]
    adapter._signing_secret = signing_key  # type: ignore[attr-defined]
    adapter._app_token = None  # type: ignore[attr-defined]
    adapter._app = None  # type: ignore[attr-defined]
    return adapter


def _sign(key: str, timestamp: str, body: bytes) -> str:
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    return "v0=" + hmac.new(key.encode("utf-8"), basestring, hashlib.sha256).hexdigest()


def _stub_app(sends: list) -> Any:
    class _Client:
        async def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
            sends.append((kwargs.get("channel"), kwargs))
            return {"ok": True}

    class _App:
        def __init__(self) -> None:
            self.client = _Client()

    return _App()


def test_capabilities_shape() -> None:
    """Slack reports mrkdwn (not markdown) and supports interactive buttons."""
    adapter = _make()
    caps = adapter.capabilities
    assert caps.streaming is True
    assert caps.markdown is False
    assert caps.rich_cards is True
    assert caps.interactive_buttons is True
    assert caps.max_message_length == 40000


@pytest.mark.asyncio
async def test_receive_filters_bot_subtype() -> None:
    adapter = _make()

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
async def test_respond_dispatches_to_app() -> None:
    adapter = _make()
    sends: list = []
    adapter._app = _stub_app(sends)  # type: ignore[attr-defined]

    await adapter.respond("m_1", {"channel_id": "C1", "text": "hi"})
    assert sends and sends[0][0] == "C1"
    assert sends[0][1]["text"] == "hi"
    assert sends[0][1]["channel"] == "C1"


@pytest.mark.asyncio
async def test_respond_requires_channel_id() -> None:
    adapter = _make()
    with pytest.raises(ValueError):
        await adapter.respond("m_1", {"text": "hi"})


@pytest.mark.asyncio
async def test_stream_collects_and_dispatches() -> None:
    adapter = _make()
    sends: list = []
    adapter._app = _stub_app(sends)  # type: ignore[attr-defined]

    async def _gen():
        yield {"channel_id": "C1"}
        yield "echo "
        yield {"text": "this"}

    await adapter.stream("m_1", _gen())
    assert sends and sends[0][0] == "C1"
    assert sends[0][1]["text"] == "echo this"


@pytest.mark.asyncio
async def test_verify_webhook_valid_signature_passes() -> None:
    adapter = _make(signing_key="shared-secret")
    body = b'{"type":"message","text":"hi"}'
    timestamp = str(int(time.time()))
    headers = {
        "x-slack-signature": _sign("shared-secret", timestamp, body),
        "x-slack-request-timestamp": timestamp,
    }
    assert await adapter.verify_webhook(headers, body) == (200, {})


@pytest.mark.asyncio
async def test_verify_webhook_bad_signature_rejected() -> None:
    adapter = _make(signing_key="shared-secret")
    body = b'{"type":"message","text":"hi"}'
    timestamp = str(int(time.time()))
    headers = {
        "x-slack-signature": _sign("wrong-secret", timestamp, body),
        "x-slack-request-timestamp": timestamp,
    }
    status, payload = await adapter.verify_webhook(headers, body)
    assert status == 401
    assert payload["error"] == "signature verification failed"


@pytest.mark.asyncio
async def test_verify_webhook_missing_headers_rejected() -> None:
    adapter = _make()
    status, payload = await adapter.verify_webhook({}, b"{}")
    assert status == 401
    assert "missing" in payload["error"]


@pytest.mark.asyncio
async def test_verify_webhook_stale_timestamp_rejected() -> None:
    adapter = _make()
    body = b"{}"
    stale = str(int(time.time()) - 3600)
    headers = {
        "x-slack-signature": _sign("signing-key", stale, body),
        "x-slack-request-timestamp": stale,
    }
    status, payload = await adapter.verify_webhook(headers, body)
    assert status == 401
    assert "replay" in payload["error"]


@pytest.mark.asyncio
async def test_verify_webhook_invalid_timestamp_rejected() -> None:
    adapter = _make()
    headers = {
        "x-slack-signature": "v0=deadbeef",
        "x-slack-request-timestamp": "not-a-number",
    }
    status, payload = await adapter.verify_webhook(headers, b"{}")
    assert status == 401
    assert "invalid" in payload["error"]
