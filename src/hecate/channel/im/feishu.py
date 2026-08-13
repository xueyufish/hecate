"""FeishuChannel — Feishu (Lark) IM platform adapter.

Thin wrapper around the official ``lark_oapi.channel.FeishuChannel`` SDK that
translates between IM-platform-specific events and Hecate's
:class:`CanonicalMessage`. Transport, signature verification, reconnection,
deduplication, rate limiting, and card-streaming throttling are delegated to
the SDK; this module only handles the Hecate-side normalization and the
response-side rendering.

The adapter is only registered when the workspace's Feishu App credentials
are present (see :mod:`hecate.gateway.registration`). At runtime, the
adapter is constructed via :func:`create_feishu_channel`, which reads
credentials from the active SecretProvider.

Design references: D1, D2 in ``openspec/changes/multi-channel-feishu-slack/design.md``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from hecate.channel.adapter import ChannelABC
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import Attachment, CanonicalMessage, MessageContent

logger = logging.getLogger(__name__)


# lark_oapi is an optional dependency (see [tools] extras). Soft import so the
# rest of Hecate can be imported without the IM SDK installed.
try:
    import lark_oapi as lark
    from lark_oapi.channel import FeishuChannel as LarkFeishuChannel

    _LARK_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded by extras
    lark = None
    LarkFeishuChannel = None
    _LARK_AVAILABLE = False


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FeishuChannel(ChannelABC):
    """Feishu (Lark) IM adapter implementing :class:`ChannelABC`.

    The adapter wraps ``lark_oapi.channel.FeishuChannel`` and adds:

    - ``receive`` — translates ``lark_oapi`` event payloads to
      :class:`CanonicalMessage`.
    - ``respond`` / ``stream`` — translates Hecate responses back to the
      Feishu API via the underlying SDK's ``send`` method.

    Args:
        app_id: Feishu App ID (e.g., ``"cli_xxx"``).
        app_secret: Feishu App secret, retrieved from SecretProvider.
        encrypt_key: Webhook/event decryption key (optional).
        verification_token: Webhook/event verification token (optional).
        transport: ``"webhook"`` (HTTP callback) or ``"ws"`` (WebSocket
            long connection). Defaults to ``"webhook"`` for server-side
            deployments; ``"ws"`` is suitable for local development.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        encrypt_key: str | None = None,
        verification_token: str | None = None,
        transport: str = "webhook",
    ) -> None:
        if not _LARK_AVAILABLE or LarkFeishuChannel is None:
            raise RuntimeError("lark_oapi is not installed. Install with: uv pip install 'hecate[tools]'")
        self._app_id = app_id
        self._app_secret = app_secret
        self._encrypt_key = encrypt_key
        self._verification_token = verification_token
        self._transport = transport
        self._underlying: Any = LarkFeishuChannel(
            app_id=app_id,
            app_secret=app_secret,
            encrypt_key=encrypt_key,
            verification_token=verification_token,
            transport=transport,
        )

    @property
    def name(self) -> str:
        return "feishu"

    @property
    def description(self) -> str:
        return "Feishu (Lark) messaging platform adapter"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            streaming=True,
            markdown=True,
            rich_cards=True,
            file_upload=True,
            max_message_length=30000,
        )

    @property
    def underlying(self) -> Any:
        """Return the underlying ``lark_oapi.channel.FeishuChannel``.

        Exposed so the webhook endpoint can call SDK-specific methods like
        ``handle_webhook_request`` for signature verification without
        re-implementing Feishu's verification protocol.
        """
        return self._underlying

    async def receive(self, raw: object) -> CanonicalMessage:
        """Convert a Feishu webhook event payload into a :class:`CanonicalMessage`.

        The :class:`lark_oapi.channel.InboundMessage` payload is expected to
        be a dict (the SDK deserializes JSON request bodies). We extract
        ``message_id``, ``sender_id`` (open_id), the flattened text, the
        ``chat_id`` (used for reply routing), and the chat type.
        """
        if not isinstance(raw, dict):
            raise TypeError(f"Feishu raw payload must be a dict, got {type(raw).__name__}")
        sender = raw.get("sender", {}) or {}
        sender_id = sender.get("sender_id") or sender.get("open_id") or raw.get("sender_id", "")
        chat_id = raw.get("chat_id", "")
        content_text = raw.get("content_text") or raw.get("text") or raw.get("content") or ""
        message_id = raw.get("message_id") or raw.get("message_id_str") or str(uuid.uuid4())
        chat_type = raw.get("chat_type", "unknown")
        resources = raw.get("resources") or []
        attachments: list[Attachment] = []
        for r in resources:
            if not isinstance(r, dict):
                continue
            rtype = r.get("type", "file")
            attachments.append(
                Attachment(
                    type=f"{rtype}/octet-stream" if rtype else "application/octet-stream",
                    url=r.get("url", ""),
                    name=r.get("name", ""),
                    size=r.get("size"),
                )
            )
        return CanonicalMessage(
            id=uuid.UUID(raw["message_uuid"]) if "message_uuid" in raw else uuid.uuid4(),
            channel_id=self.name,
            user_id=str(sender_id),
            session_id=None,
            content=MessageContent(text=str(content_text) if content_text else None, attachments=tuple(attachments)),
            metadata={
                "chat_id": chat_id,
                "chat_type": chat_type,
                "message_id": message_id,
                "mentioned_bot": raw.get("mentioned_bot", False),
                "mentioned_all": raw.get("mentioned_all", False),
            },
            timestamp=raw.get("create_time") or raw.get("timestamp") or _utcnow(),
        )

    async def respond(self, message_id: str, response: object) -> None:
        """Send a response back to Feishu.

        ``response`` must be a dict with keys:

        - ``chat_id`` (required): target Feishu chat identifier.
        - ``text`` or ``card`` (required): the payload to send.

        Any ``Outbound*`` dataclass from ``lark_oapi`` is also accepted.
        """
        if not isinstance(response, dict):
            raise TypeError(f"Feishu response must be a dict, got {type(response).__name__}")
        chat_id = response.get("chat_id")
        if not chat_id:
            raise ValueError("Feishu response must include 'chat_id'")
        # Strip routing keys from the payload before handing to the SDK.
        payload = {k: v for k, v in response.items() if k != "chat_id"}
        if "text" not in payload and "card" not in payload and "markdown" not in payload:
            payload["text"] = ""
        await self._underlying.send(chat_id, payload)

    async def stream(self, message_id: str, chunks: AsyncIterator[object]) -> None:
        """MVP streaming — collect chunks and dispatch as a single response.

        Full streaming card updates (CardKit preallocation + throttling) are
        deferred to Phase 2 (see design.md D5). For MVP, we await all chunks,
        concatenate the ``text`` field, and call :meth:`respond`.
        """
        text_parts: list[str] = []
        chat_id: str | None = None
        async for chunk in chunks:
            if isinstance(chunk, dict):
                if "chat_id" in chunk and chat_id is None:
                    chat_id = str(chunk["chat_id"])
                if "text" in chunk and chunk["text"]:
                    text_parts.append(str(chunk["text"]))
            elif isinstance(chunk, str):
                text_parts.append(chunk)
        if not chat_id:
            logger.warning("FeishuChannel.stream: no chat_id found in chunks; cannot send response")
            return
        await self.respond(
            message_id,
            {"chat_id": chat_id, "text": "".join(text_parts)},
        )


def create_feishu_channel(
    app_id: str,
    app_secret: str,
    encrypt_key: str | None = None,
    verification_token: str | None = None,
    transport: str = "webhook",
) -> FeishuChannel:
    """Factory that constructs a :class:`FeishuChannel` and is friendlier to
    plugin-registry instantiation than the ``__init__`` constructor.
    """
    return FeishuChannel(
        app_id=app_id,
        app_secret=app_secret,
        encrypt_key=encrypt_key,
        verification_token=verification_token,
        transport=transport,
    )
