"""SlackChannel — Slack IM platform adapter.

Thin wrapper around the official ``slack_bolt.App`` SDK that translates
between Slack events and Hecate's :class:`CanonicalMessage`. Transport
(Socket Mode / Webhook), signature verification, and OAuth are delegated to
the SDK; this module handles the Hecate-side normalization and response
rendering.

Design references: D1, D2 in ``openspec/changes/multi-channel-feishu-slack/design.md``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from hecate.channel.adapter import ChannelBase
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import Attachment, CanonicalMessage, MessageContent

logger = logging.getLogger(__name__)


# slack_bolt is an optional dependency (see [tools] extras).
try:
    from slack_bolt import App as SlackBoltApp

    _SLACK_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded by extras
    SlackBoltApp = None
    _SLACK_AVAILABLE = False


class SlackChannel(ChannelBase):
    """Slack IM adapter implementing :class:`ChannelBase`.

    Wraps ``slack_bolt.App`` and exposes Hecate's ``receive/respond/stream``
    contract.

    Args:
        bot_token: Slack Bot User OAuth Token (``xoxb-...``).
        signing_secret: Slack Signing Secret used by ``RequestVerification``
            middleware to validate inbound HTTP requests.
        app_token: Slack App-Level Token (``xapp-...``) for Socket Mode.
            Optional; required only when running in Socket Mode.
    """

    def __init__(
        self,
        bot_token: str,
        signing_secret: str,
        app_token: str | None = None,
        token_verification_enabled: bool = True,
    ) -> None:
        if not _SLACK_AVAILABLE or SlackBoltApp is None:
            raise RuntimeError("slack_bolt is not installed. Install with: uv pip install 'hecate[tools]'")
        self._bot_token = bot_token
        self._signing_secret = signing_secret
        self._app_token = app_token
        # ``token_verification_enabled`` defaults to True; tests pass
        # ``False`` to skip the auth.test round-trip on instantiation.
        self._app: Any = SlackBoltApp(
            token=bot_token,
            signing_secret=signing_secret,
            token_verification_enabled=token_verification_enabled,
        )

    @property
    def name(self) -> str:
        return "slack"

    @property
    def description(self) -> str:
        return "Slack messaging platform adapter"

    @property
    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            streaming=True,
            markdown=False,  # Slack uses mrkdwn, not standard markdown
            rich_cards=True,
            interactive_buttons=True,
            file_upload=True,
            max_message_length=40000,
        )

    @property
    def underlying_app(self) -> Any:
        """Expose the underlying :class:`slack_bolt.App` for advanced wiring.

        Used by the webhook endpoint to register Bolt event listeners when
        delegating URL verification to the SDK.
        """
        return self._app

    async def receive(self, raw: object) -> CanonicalMessage:
        """Convert a Slack ``event_callback`` payload to a :class:`CanonicalMessage`.

        Expects ``raw`` to be the Slack ``event`` sub-object (the
        ``event_callback`` wrapper is unwrapped by the webhook handler
        before invocation).

        Filters out messages with a non-null ``subtype`` (bot messages,
        edits, joins, etc.) to avoid bot-loop feedback. Callers that need
        to handle these explicitly should bypass :meth:`receive`.
        """
        if not isinstance(raw, dict):
            raise TypeError(f"Slack raw payload must be a dict, got {type(raw).__name__}")
        subtype = raw.get("subtype")
        if subtype:
            raise ValueError(
                f"Slack message subtype '{subtype}' is not handled by receive(); bypass for non-message events"
            )
        text = raw.get("text") or ""
        user_id = raw.get("user") or raw.get("bot_id") or ""
        channel_id = raw.get("channel") or ""
        ts = raw.get("ts") or raw.get("event_ts") or ""
        files = raw.get("files") or []
        attachments: list[Attachment] = []
        for f in files:
            if not isinstance(f, dict):
                continue
            attachments.append(
                Attachment(
                    type=f.get("mimetype", "application/octet-stream"),
                    url=f.get("url_private", ""),
                    name=f.get("name", ""),
                    size=f.get("size"),
                )
            )
        return CanonicalMessage(
            id=uuid.uuid4(),
            channel_id=self.name,
            user_id=str(user_id),
            session_id=None,
            content=MessageContent(text=text if text else None, attachments=tuple(attachments)),
            metadata={
                "channel_id": channel_id,
                "ts": ts,
                "thread_ts": raw.get("thread_ts", ""),
                "team_id": raw.get("team", ""),
                "event_type": raw.get("type", "message"),
            },
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    async def respond(self, message_id: str, response: object) -> None:
        """Send a response to Slack.

        ``response`` must be a dict with keys:

        - ``channel_id`` (required): target Slack channel ID.
        - ``text`` or ``blocks`` (required): the message payload.

        Plain text is auto-converted to Slack ``mrkdwn`` when ``blocks`` is
        not provided. ``chat.postMessage`` is invoked via the Bolt app's
        underlying client.
        """
        if not isinstance(response, dict):
            raise TypeError(f"Slack response must be a dict, got {type(response).__name__}")
        channel_id = response.get("channel_id")
        if not channel_id:
            raise ValueError("Slack response must include 'channel_id'")
        kwargs: dict[str, Any] = {"channel": channel_id}
        if "blocks" in response:
            kwargs["blocks"] = response["blocks"]
        elif "text" in response:
            kwargs["text"] = response["text"]
        else:
            kwargs["text"] = ""
        if "thread_ts" in response:
            kwargs["thread_ts"] = response["thread_ts"]
        client = self._app.client
        await client.chat_postMessage(**kwargs)

    async def stream(self, message_id: str, chunks: AsyncIterator[object]) -> None:
        """MVP streaming — collect chunks and dispatch as a single response.

        Full streaming updates (incremental ``chat.update``) are deferred to
        Phase 2 (see design.md D5). For MVP, chunks are concatenated and
        posted via :meth:`respond`.
        """
        text_parts: list[str] = []
        channel_id: str | None = None
        thread_ts: str | None = None
        async for chunk in chunks:
            if isinstance(chunk, dict):
                if "channel_id" in chunk and channel_id is None:
                    channel_id = str(chunk["channel_id"])
                if "thread_ts" in chunk and thread_ts is None:
                    thread_ts = str(chunk["thread_ts"])
                if "text" in chunk and chunk["text"]:
                    text_parts.append(str(chunk["text"]))
            elif isinstance(chunk, str):
                text_parts.append(chunk)
        if not channel_id:
            logger.warning("SlackChannel.stream: no channel_id found in chunks; cannot send response")
            return
        payload: dict[str, Any] = {"channel_id": channel_id, "text": "".join(text_parts)}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        await self.respond(message_id, payload)


def create_slack_channel(
    bot_token: str,
    signing_secret: str,
    app_token: str | None = None,
    token_verification_enabled: bool = True,
) -> SlackChannel:
    """Factory constructing a :class:`SlackChannel`."""
    return SlackChannel(
        bot_token=bot_token,
        signing_secret=signing_secret,
        app_token=app_token,
        token_verification_enabled=token_verification_enabled,
    )
