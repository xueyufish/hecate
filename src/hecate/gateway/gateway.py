"""Gateway — routes messages from channels to the agent runtime.

The Gateway is the central routing layer between channel adapters and
WorkflowExecutionService. It accepts CanonicalMessage from channels,
resolves session context via SessionRouter, and delegates execution.
"""

from __future__ import annotations

import logging

from hecate.channel.types import CanonicalMessage
from hecate.gateway.session import SessionRouter

logger = logging.getLogger(__name__)


class Gateway:
    """Routes CanonicalMessage from channels to the agent runtime.

    Args:
        session_router: Optional SessionRouter instance. Creates a new
            one if not provided.
    """

    def __init__(self, session_router: SessionRouter | None = None) -> None:
        self._session_router = session_router or SessionRouter()

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

        # Resolve session (create or resume)
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

        # TODO: Delegate to WorkflowExecutionService when integrating
        # with the existing agent runtime. For now, the Gateway just
        # resolves the session and returns it.
        return session_id

    @property
    def session_router(self) -> SessionRouter:
        """Access the underlying SessionRouter."""
        return self._session_router
