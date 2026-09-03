"""Tests for IM channel registration and MessageBus lifecycle.

Covers:
- ``register_im_channels`` picks up Feishu / Slack adapters when
  environment variables are present and skips silently otherwise.
- ``IMMessageBus.start`` / ``stop`` bring the consumer workers up and down.
- ``IMMessageBus.enqueue`` works against a real consumer that records
  delivered envelopes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.gateway.registration import register_im_channels
from hecate.channel.im.message_bus import IMMessageBus
from hecate.core.plugin.registry import PluginRegistry


def _stub_workflow_service(records: list[dict[str, Any]]) -> Any:
    """Workflow service stub that records each execute call."""

    class _Service:
        async def execute(self, **_kwargs: Any) -> dict[str, str]:
            records.append({"called": True})
            return {"text": "echo"}

    return _Service()


@pytest.mark.asyncio
async def test_register_im_channels_without_env_vars_registers_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No IM credentials -> no adapters registered."""
    for var in (
        "HECATE_IM_FEISHU_APP_ID",
        "HECATE_IM_FEISHU_APP_SECRET",
        "HECATE_IM_SLACK_BOT_TOKEN",
        "HECATE_IM_SLACK_SIGNING_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    registry = PluginRegistry()
    count = register_im_channels(registry)
    assert count == 0
    assert registry.list_all().get("channel", {}) == {}


@pytest.mark.asyncio
async def test_register_im_channels_feishu_when_credentials_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feishu credentials present -> exactly one adapter registered as 'feishu'."""
    # The adapter lives in the hecate-channel-feishu plugin package (PR5b).
    # Stub its module-level availability flag and SDK class so registration
    # logic is exercised without the real SDK; adapter behavior is covered
    # by tests/test_channels/test_feishu_channel.py.
    import hecate_channel_feishu.channel as feishu_mod

    class _FakeLarkFeishuChannel:
        def __init__(self, **_: object) -> None:
            pass

    monkeypatch.setattr(feishu_mod, "_LARK_AVAILABLE", True)
    monkeypatch.setattr(feishu_mod, "LarkFeishuChannel", _FakeLarkFeishuChannel)
    monkeypatch.setenv("HECATE_IM_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("HECATE_IM_FEISHU_APP_SECRET", "secret-test")
    monkeypatch.delenv("HECATE_IM_FEISHU_ENCRYPT_KEY", raising=False)
    monkeypatch.delenv("HECATE_IM_FEISHU_VERIFICATION_TOKEN", raising=False)
    monkeypatch.delenv("HECATE_IM_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HECATE_IM_SLACK_SIGNING_SECRET", raising=False)
    registry = PluginRegistry()
    count = register_im_channels(registry)
    assert count == 1
    adapter = registry.get_by_name("channel", "feishu")
    assert adapter is not None
    assert adapter.name == "feishu"


@pytest.mark.asyncio
async def test_register_im_channels_slack_when_credentials_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack credentials present -> adapter registered as 'slack'.

    The Slack registration path uses ``token_verification_enabled=False``
    so tests do not contact Slack's auth.test endpoint during
    instantiation. The adapter lives in the hecate-channel-slack plugin
    package (PR5b); its module-level availability flag and SDK class are
    stubbed so registration logic runs without the real SDK.
    """
    import hecate_channel_slack.channel as slack_mod

    class _FakeSlackBoltApp:
        def __init__(self, **_: object) -> None:
            pass

    monkeypatch.setattr(slack_mod, "_SLACK_AVAILABLE", True)
    monkeypatch.setattr(slack_mod, "SlackBoltApp", _FakeSlackBoltApp)
    monkeypatch.delenv("HECATE_IM_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HECATE_IM_FEISHU_APP_SECRET", raising=False)
    monkeypatch.setenv("HECATE_IM_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("HECATE_IM_SLACK_SIGNING_SECRET", "signing-test")
    monkeypatch.setenv("HECATE_IM_SLACK_TEST_MODE", "1")
    registry = PluginRegistry()
    count = register_im_channels(registry)
    assert count == 1
    adapter = registry.get_by_name("channel", "slack")
    assert adapter is not None
    assert adapter.name == "slack"


@pytest.mark.asyncio
async def test_message_bus_start_and_stop() -> None:
    """``start`` brings up consumers; ``stop`` drains them."""
    bus = IMMessageBus()
    await bus.start(worker_count=2)
    assert bus._queue is not None
    assert len(bus._workers) == 2
    await bus.stop()
    assert bus._workers == []


@pytest.mark.asyncio
async def test_message_bus_enqueues_and_consumes() -> None:
    """A message enqueued via the bus is delivered to a background consumer."""
    from uuid import uuid4

    from hecate.channel.capabilities import ChannelCapabilities
    from hecate.channel.types import CanonicalMessage, MessageContent

    class _Adapter:
        name = "test"
        description = "test"
        capabilities = ChannelCapabilities(markdown=True)

        async def receive(self, raw: object) -> CanonicalMessage:
            raise NotImplementedError

        async def respond(self, message_id: str, response: object) -> None:
            pass

        async def stream(self, message_id: str, chunks: object) -> None:
            pass

    records: list[dict[str, Any]] = []
    bus = IMMessageBus(workflow_service=_stub_workflow_service(records))
    await bus.start(worker_count=1)
    try:
        canonical = CanonicalMessage(
            id=uuid4(),
            channel_id="test",
            user_id="u1",
            session_id=None,
            content=MessageContent(text="hello"),
            metadata={"chat_id": "chat-1"},
        )
        adapter = _Adapter()
        await bus.enqueue(
            canonical_message=canonical,
            adapter=adapter,
            workspace_id=__import__("uuid").uuid4(),
            chat_id="chat-1",
            channel_capabilities=ChannelCapabilities(markdown=True),
        )
        # Wait for the consumer to process the message.
        for _ in range(50):
            if records:
                break
            await asyncio.sleep(0.05)
        assert records, "consumer did not process the envelope in time"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_message_bus_worker_errors_are_logged_not_raised() -> None:
    """A workflow-service exception is logged, never propagated."""

    class _Service:
        async def execute(self, **_kwargs: Any) -> dict[str, str]:
            raise RuntimeError("simulated workflow failure")

    class _Adapter:
        name = "test"
        description = "test"
        capabilities = ChannelCapabilities(markdown=False)

        async def receive(self, raw: object) -> object:
            raise NotImplementedError

        async def respond(self, message_id: str, response: object) -> None:
            pass

        async def stream(self, message_id: str, chunks: object) -> None:
            pass

    bus = IMMessageBus(workflow_service=_Service())
    await bus.start(worker_count=1)
    try:
        from uuid import uuid4

        from hecate.channel.types import CanonicalMessage, MessageContent

        canonical = CanonicalMessage(
            id=uuid4(),
            channel_id="test",
            user_id="u1",
            session_id=None,
            content=MessageContent(text="hello"),
            metadata={"chat_id": "chat-1"},
        )
        await bus.enqueue(
            canonical_message=canonical,
            adapter=_Adapter(),
            workspace_id=__import__("uuid").uuid4(),
            chat_id="chat-1",
            channel_capabilities=ChannelCapabilities(),
        )
        # Allow the worker to consume and log the error.
        await asyncio.sleep(0.3)
        # The worker is still running (no exception propagated).
        assert len(bus._workers) == 1
    finally:
        await bus.stop()
