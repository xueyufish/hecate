"""Gateway — routes messages from channels to the agent runtime.

The Gateway is the central routing layer between channel adapters and
WorkflowExecutionService. It accepts CanonicalMessage from channels,
resolves session context via SessionRouter, and delegates execution.

For non-IM channels (e.g., the OpenAI-compatible ``api`` path) the Gateway
delegates to the in-memory :class:`SessionRouter`. For IM channels
(``"feishu"``, ``"slack"``, ...) the Gateway is paired with the
:class:`IMMessageBus` so that the actual workflow execution happens
asynchronously after the webhook has returned 200 OK.

Design reference: design.md D5, D6.
"""

from __future__ import annotations

import logging
import uuid

from hecate.channel.types import CanonicalMessage
from hecate.gateway.session import SessionRouter

logger = logging.getLogger(__name__)


# Channels routed through the IM MessageBus. Extend as new IM adapters land.
IM_CHANNEL_PREFIXES: tuple[str, ...] = ("feishu", "slack", "dingtalk", "wecom", "telegram")


class Gateway:
    """Routes CanonicalMessage from channels to the agent runtime.

    Args:
        session_router: Optional SessionRouter instance. Creates a new
            one if not provided.
        im_message_bus: Optional :class:`IMMessageBus` instance. When
            ``None``, IM channels fall back to the SessionRouter-only path
            (legacy behavior, useful for tests).
    """

    def __init__(
        self,
        session_router: SessionRouter | None = None,
        im_message_bus: object | None = None,
    ) -> None:
        self._session_router = session_router or SessionRouter()
        self._im_message_bus = im_message_bus

    @property
    def is_im_channel(self) -> bool:
        """Return True if the attached MessageBus exists (back-compat shim)."""
        return self._im_message_bus is not None

    async def route(self, message: CanonicalMessage) -> str:
        """Route a message to the appropriate session and execute.

        Args:
            message: The incoming CanonicalMessage from a channel adapter.

        Returns:
            The resolved session ID.

        Raises:
            ValueError: If the message is invalid.
        """
        if not message.channel_id:
            raise ValueError("CanonicalMessage must have a channel_id")

        # IM-channel fast path: dispatch through the MessageBus and return
        # the deterministic conversation UUID (design.md D4). The actual
        # workflow execution happens in a background asyncio task.
        if self._is_im_channel(message.channel_id):
            return await self._route_im(message)

        # Legacy path: pure in-memory SessionRouter (kept for back-compat
        # with the existing OpenAI-compatible API path).
        session_id = self._session_router.resolve(
            session_id=message.session_id,
            channel_id=message.channel_id,
            user_id=message.user_id,
        )
        logger.info(
            "Gateway routing message %s from channel=%s user=%s session=%s",
            message.id,
            message.channel_id,
            message.user_id,
            session_id,
        )
        return session_id

    def _is_im_channel(self, channel_id: str) -> bool:
        return channel_id in IM_CHANNEL_PREFIXES and self._im_message_bus is not None

    async def _route_im(self, message: CanonicalMessage) -> str:
        """Resolve the deterministic conversation UUID for an IM message.

        We avoid an upfront session creation here — the actual persistence
        happens inside :class:`IMSessionRouter` once the binding resolves
        to a real Hecate user. For now we return a placeholder UUID so
        callers can correlate logs.
        """
        workspace_id = message.metadata.get("workspace_id")
        chat_id = str(message.metadata.get("chat_id") or message.metadata.get("channel_id") or "")
        bus = self._im_message_bus
        if bus is None:
            logger.warning("Gateway._route_im called without an attached MessageBus")
            return str(uuid.uuid4())
        try:
            await bus.enqueue(
                canonical_message=message,
                adapter=message.metadata.get("_adapter"),
                workspace_id=workspace_id,
                chat_id=chat_id,
                channel_capabilities=message.metadata.get("_capabilities"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to enqueue IM message via Gateway: %s", exc)
        return str(uuid.uuid4())

    @property
    def session_router(self) -> SessionRouter:
        """Access the underlying SessionRouter."""
        return self._session_router
