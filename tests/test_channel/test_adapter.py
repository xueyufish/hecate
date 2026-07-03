"""Tests for ChannelABC, CanonicalMessage, and ChannelCapabilities."""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from hecate.channel.adapter import ChannelABC
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


class TestChannelABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ChannelABC()  # type: ignore[abstract]

    def test_concrete_subclass(self) -> None:
        class TestChannel(ChannelABC):
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
