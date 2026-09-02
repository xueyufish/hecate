"""Tests for ChannelBase, CanonicalMessage, and ChannelCapabilities."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from hecate.channel.adapter import ChannelBase
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import Attachment, CanonicalMessage, MessageContent


class TestCanonicalMessage:
    def test_create_with_text(self) -> None:
        msg = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="test",
            user_id="user1",
            session_id=None,
            content=MessageContent(text="hello"),
        )
        assert msg.content.text == "hello"
        assert msg.content.attachments == ()
        assert msg.channel_id == "test"

    def test_immutable(self) -> None:
        msg = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="test",
            user_id="user1",
            session_id=None,
            content=MessageContent(text="hello"),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            msg.content = MessageContent(text="changed")  # type: ignore[misc]

    def test_metadata_passthrough(self) -> None:
        msg = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="telegram",
            user_id="user1",
            session_id=None,
            content=MessageContent(text="hi"),
            metadata={"telegram_chat_id": "123"},
        )
        assert msg.metadata == {"telegram_chat_id": "123"}

    def test_with_attachments(self) -> None:
        att = Attachment(type="image/png", url="https://example.com/img.png", name="img.png", size=1024)
        msg = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="test",
            user_id="user1",
            session_id=None,
            content=MessageContent(text=None, attachments=(att,)),
        )
        assert len(msg.content.attachments) == 1
        assert msg.content.attachments[0].type == "image/png"


class TestChannelCapabilities:
    def test_defaults(self) -> None:
        caps = ChannelCapabilities()
        assert caps.streaming is False
        assert caps.interactive_buttons is False
        assert caps.file_upload is False
        assert caps.markdown is False
        assert caps.rich_cards is False
        assert caps.max_message_length is None

    def test_immutable(self) -> None:
        caps = ChannelCapabilities()
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.streaming = True  # type: ignore[misc]

    def test_custom_values(self) -> None:
        caps = ChannelCapabilities(streaming=True, markdown=True, max_message_length=4096)
        assert caps.streaming is True
        assert caps.markdown is True
        assert caps.max_message_length == 4096


class TestChannelBase:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ChannelBase()  # type: ignore[abstract]

    def test_concrete_subclass(self) -> None:
        class TestChannel(ChannelBase):
            @property
            def name(self) -> str:
                return "test"

            @property
            def description(self) -> str:
                return "Test channel"

            @property
            def capabilities(self) -> ChannelCapabilities:
                return ChannelCapabilities()

            async def receive(self, raw: object) -> CanonicalMessage:
                return CanonicalMessage(
                    id=uuid.uuid4(),
                    channel_id="test",
                    user_id="u",
                    session_id=None,
                    content=MessageContent(text=str(raw)),
                )

            async def respond(self, message_id: str, response: object) -> None:
                pass

            async def stream(self, message_id: str, chunks: object) -> None:
                pass

        ch = TestChannel()
        assert ch.name == "test"
        assert ch.description == "Test channel"


class TestChannelBaseDefaultHooks:
    """PR5a optional hooks — defaults must be no-ops so existing
    subclasses (notification adapters, im adapters, plugin SDK) stay
    source-compatible without overriding them."""

    def _make(self) -> ChannelBase:
        class TestChannel(ChannelBase):
            @property
            def name(self) -> str:
                return "test"

            @property
            def description(self) -> str:
                return "Test channel"

            @property
            def capabilities(self) -> ChannelCapabilities:
                return ChannelCapabilities()

            async def receive(self, raw: object) -> CanonicalMessage:
                return CanonicalMessage(
                    id=uuid.uuid4(),
                    channel_id="test",
                    user_id="u",
                    session_id=None,
                    content=MessageContent(text=str(raw)),
                )

            async def respond(self, message_id: str, response: object) -> None:
                pass

            async def stream(self, message_id: str, chunks: object) -> None:
                pass

        return TestChannel()

    async def test_verify_webhook_default_continues(self) -> None:
        """Default (200, {}) means 'not platform-verified, continue to receive'."""
        status, payload = await self._make().verify_webhook({"x": "1"}, b"body")
        assert status == 200
        assert payload == {}

    async def test_health_check_default_ok(self) -> None:
        assert await self._make().health_check() == "ok"

    async def test_lifecycle_hooks_default_noop(self) -> None:
        ch = self._make()
        await ch.on_load()  # must not raise
        await ch.on_unload()  # must not raise
