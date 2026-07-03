"""Tests for Gateway session routing."""

from __future__ import annotations

import uuid

from hecate.channel.types import CanonicalMessage, MessageContent
from hecate.gateway.gateway import Gateway
from hecate.gateway.session import SessionRouter


class TestSessionRouter:
    def test_resolve_new_session(self) -> None:
        router = SessionRouter()
        session_id = router.resolve(None, "feishu", "user1")
        assert session_id is not None
        assert router.active_count == 1

    def test_resolve_existing_session(self) -> None:
        router = SessionRouter()
        sid = router.resolve(None, "feishu", "user1")
        result = router.resolve(sid, "slack", "user2")
        assert result == sid
        assert router.active_count == 1

    def test_get_session_context(self) -> None:
        router = SessionRouter()
        sid = router.resolve(None, "feishu", "user1")
        ctx = router.get_session_context(sid)
        assert ctx == ("feishu", "user1")

    def test_get_session_context_not_found(self) -> None:
        router = SessionRouter()
        assert router.get_session_context("nonexistent") is None

    def test_remove_session(self) -> None:
        router = SessionRouter()
        sid = router.resolve(None, "feishu", "user1")
        router.remove(sid)
        assert router.active_count == 0

    def test_resolve_with_explicit_session_id(self) -> None:
        router = SessionRouter()
        sid = router.resolve("my-session", "feishu", "user1")
        assert sid == "my-session"


class TestGateway:
    async def test_route_creates_session(self) -> None:
        gateway = Gateway()
        msg = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="feishu",
            user_id="user1",
            session_id=None,
            content=MessageContent(text="hello"),
        )
        session_id = await gateway.route(msg)
        assert session_id is not None
        assert gateway.session_router.active_count == 1

    async def test_route_resumes_session(self) -> None:
        gateway = Gateway()
        msg1 = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="feishu",
            user_id="user1",
            session_id=None,
            content=MessageContent(text="hello"),
        )
        sid1 = await gateway.route(msg1)

        msg2 = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="feishu",
            user_id="user1",
            session_id=sid1,
            content=MessageContent(text="world"),
        )
        sid2 = await gateway.route(msg2)
        assert sid1 == sid2

    async def test_route_empty_channel_id_raises(self) -> None:
        gateway = Gateway()
        msg = CanonicalMessage(
            id=uuid.uuid4(),
            channel_id="",
            user_id="user1",
            session_id=None,
            content=MessageContent(text="hello"),
        )
        import pytest

        with pytest.raises(ValueError, match="channel_id"):
            await gateway.route(msg)
