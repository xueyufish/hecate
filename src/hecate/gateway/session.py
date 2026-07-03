"""SessionRouter — maps session IDs to channel and user context.

The SessionRouter maintains a mapping of session_id → (channel_id, user_id)
so that the Gateway can route messages to the correct session regardless
of which channel they arrived from.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


class SessionRouter:
    """Routes messages to sessions based on session_id.

    When a message arrives with an existing session_id, the router
    returns the stored (channel_id, user_id) pair. When session_id
    is None, a new session is created.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[str, str]] = {}

    def resolve(self, session_id: str | None, channel_id: str, user_id: str) -> str:
        """Resolve a session ID, creating one if needed.

        Args:
            session_id: Existing session ID or None for new session.
            channel_id: Source channel identifier.
            user_id: Platform-specific user identifier.

        Returns:
            The resolved session ID (existing or newly created).
        """
        if session_id is not None and session_id in self._sessions:
            return session_id

        new_id = session_id or str(uuid.uuid4())
        self._sessions[new_id] = (channel_id, user_id)
        logger.debug("Session %s created for channel=%s user=%s", new_id, channel_id, user_id)
        return new_id

    def get_session_context(self, session_id: str) -> tuple[str, str] | None:
        """Get the (channel_id, user_id) for a session.

        Args:
            session_id: The session identifier.

        Returns:
            Tuple of (channel_id, user_id) or None if session not found.
        """
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        """Remove a session from the router.

        Args:
            session_id: The session identifier to remove.
        """
        self._sessions.pop(session_id, None)

    @property
    def active_count(self) -> int:
        """Return the number of active sessions."""
        return len(self._sessions)
