"""NotificationChannelAdapter — base class and built-in notification channels.

Wraps existing notification render functions as Channel ABC implementations.
Notification channels are outbound-only: receive() is a no-op, respond()
sends the notification to the target platform.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from hecate.channel.adapter import ChannelABC
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import CanonicalMessage

logger = logging.getLogger(__name__)


class NotificationChannelAdapter(ChannelABC):
    """Base class for outbound notification channel adapters.

    Subclasses implement :pymeth:`respond` with platform-specific
    dispatch logic. The :pymeth:`receive` method is a no-op since
    notification channels are outbound-only.
    """

    @property
    def capabilities(self) -> ChannelCapabilities:
        """Notification channels support markdown by default."""
        return ChannelCapabilities(markdown=True)

    async def receive(self, raw: object) -> CanonicalMessage:
        """No-op — notification channels are outbound-only."""
        raise NotImplementedError("Notification channels do not receive messages")

    async def stream(self, message_id: str, chunks: AsyncIterator[object]) -> None:
        """No-op — notification channels do not support streaming."""
        raise NotImplementedError("Notification channels do not support streaming")


class WebhookNotificationAdapter(NotificationChannelAdapter):
    """Sends notifications via HTTP webhook with retry logic.

    The payload is rendered by the provided ``render`` callable, which
    accepts (event, rule) and returns a JSON-serializable dict.
    """

    def __init__(
        self,
        channel_name: str,
        channel_description: str,
        render_fn: Any,
    ) -> None:
        self._name = channel_name
        self._description = channel_description
        self._render_fn = render_fn

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    async def respond(self, message_id: str, response: object) -> None:
        """Send webhook notification with exponential backoff retry."""
        # response is expected to be a dict with 'event', 'rule', 'url' keys
        data: dict[str, Any] = response
        url = data.get("url", "")
        if not url:
            logger.warning("Webhook channel %s has no URL configured", self._name)
            return

        event = data["event"]
        rule = data["rule"]
        payload = self._render_fn(event, rule)

        backoff_times = [1, 2, 4]
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code < 500:
                        return
                    logger.warning("Webhook %s returned %d (attempt %d)", url, resp.status_code, attempt + 1)
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                logger.warning("Webhook %s failed (attempt %d): %s", url, attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(backoff_times[attempt])
        logger.error("Webhook %s failed after 3 retries", url)


class WebSocketNotificationAdapter(NotificationChannelAdapter):
    """Broadcasts notifications via WebSocket to connected clients."""

    def __init__(self, connection_manager: Any = None) -> None:
        self._connection_manager = connection_manager

    @property
    def name(self) -> str:
        return "websocket"

    @property
    def description(self) -> str:
        return "WebSocket broadcast notification channel"

    async def respond(self, message_id: str, response: object) -> None:
        """Broadcast alert via WebSocket."""
        if self._connection_manager is None:
            return
        data: dict[str, Any] = response
        await self._connection_manager.broadcast(data.get("message", {}))


class EmailNotificationAdapter(NotificationChannelAdapter):
    """Sends notifications via SMTP email."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "email"

    @property
    def description(self) -> str:
        return "SMTP email notification channel"

    async def respond(self, message_id: str, response: object) -> None:
        """Send email notification via SMTP."""
        # response is expected to be a dict with 'subject', 'html_body', 'recipients', 'config'
        data: dict[str, Any] = response
        recipients = data.get("recipients", [])
        if not recipients:
            return

        subject = data.get("subject", "")
        html_body = data.get("html_body", "")
        config = data.get("config", {})

        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            import aiosmtplib

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = config.get("smtp_from", "")
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(html_body, "html"))

            await aiosmtplib.send(
                msg,
                hostname=config.get("smtp_host", ""),
                port=config.get("smtp_port", 587),
                username=config.get("smtp_user") or None,
                password=config.get("smtp_password") or None,
                recipients=recipients,
            )
        except ImportError:
            logger.warning("aiosmtplib not installed — email alert skipped")
        except Exception as e:
            logger.warning("Email send failed: %s", e)
