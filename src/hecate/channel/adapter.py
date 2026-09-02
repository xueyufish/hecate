"""ChannelBase — abstract interface for external platform adapters.

All channel adapters — built-in or third-party — must implement this
interface to be registered with the PluginRegistry under type="channel".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import CanonicalMessage


class ChannelBase(ABC):
    """Abstract base class for external platform adapters.

    Each channel adapter converts a specific platform's messages
    (Feishu, Slack, Telegram, Email, etc.) to and from CanonicalMessage.

    Subclasses must define:

    - :pyattr:`name` — short identifier (e.g., ``"feishu"``, ``"slack"``)
    - :pyattr:`description` — human-readable explanation
    - :pyattr:`capabilities` — what features this channel supports
    - :pymeth:`receive` — convert platform-specific raw input to CanonicalMessage
    - :pymeth:`respond` — send a response back to the platform
    - :pymeth:`stream` — stream a response back to the platform

    Example::

        class FeishuChannel(ChannelBase):
            @property
            def name(self) -> str:
                return "feishu"

            @property
            def description(self) -> str:
                return "Feishu (Lark) messaging platform adapter"

            @property
            def capabilities(self) -> ChannelCapabilities:
                return ChannelCapabilities(streaming=True, markdown=True, rich_cards=True)

            async def receive(self, raw: dict) -> CanonicalMessage:
                return CanonicalMessage(...)

            async def respond(self, message_id: str, response: OutgoingMessage) -> None:
                # Send to Feishu API
                ...

            async def stream(self, message_id: str, chunks: AsyncIterator[StreamChunk]) -> None:
                # Stream to Feishu
                ...
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this channel (e.g., ``"feishu"``, ``"slack"``)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of this channel adapter."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> ChannelCapabilities:
        """Declare what features this channel supports."""
        ...

    @abstractmethod
    async def receive(self, raw: object) -> CanonicalMessage:
        """Convert platform-specific raw input into a CanonicalMessage.

        Args:
            raw: Platform-specific input (e.g., Feishu event payload,
                Slack event, Telegram update).

        Returns:
            A :class:`CanonicalMessage` representing the incoming message.
        """
        ...

    @abstractmethod
    async def respond(self, message_id: str, response: object) -> None:
        """Send a response back to the platform.

        Args:
            message_id: The original message ID being responded to.
            response: Platform-specific response payload.
        """
        ...

    @abstractmethod
    async def stream(self, message_id: str, chunks: AsyncIterator[object]) -> None:
        """Stream a response back to the platform.

        Args:
            message_id: The original message ID being responded to.
            chunks: Async iterator of response chunks.
        """
        ...

    # ------------------------------------------------------------------
    # Optional lifecycle / verification hooks (PR5a). All default to
    # no-ops so existing subclasses stay source-compatible; channels
    # override only what their platform needs. The webhook router uses
    # ``verify_webhook`` in preference to reaching into SDK internals
    # (``adapter.underlying``), which is the seam PR5b's package split
    # relies on.
    # ------------------------------------------------------------------

    async def on_load(self) -> None:
        """Lifecycle hook invoked after registration. Default no-op."""
        return None

    async def on_unload(self) -> None:
        """Lifecycle hook invoked before shutdown. Default no-op."""
        return None

    async def health_check(self) -> str:
        """Return a coarse health signal for ops dashboards.

        Default ``"ok"``; adapters with remote dependencies should probe
        their platform and return a short human-readable status instead.
        """
        return "ok"

    async def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> tuple[int, dict]:  # noqa: RUF012
        """Verify a webhook request's authenticity and optionally decrypt it.

        Adapters whose platform enforces signed / encrypted webhooks
        override this (e.g., Feishu delegates to ``lark_oapi``'s handler;
        Slack relies on ``slack_bolt`` middleware and keeps the default).
        The webhook router calls this before ``receive``; returning a
        non-200 status short-circuits the request with that response.

        Args:
            headers: The incoming request's headers.
            raw_body: The raw (undecoded) request body.

        Returns:
            ``(status_code, response_payload)`` — default ``(200, {})``
            meaning "not platform-verified, continue to receive()".
        """
        return 200, {}
