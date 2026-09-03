"""Session lock manager for per-session sequential processing.

Ensures that only one message is processed at a time within a single
conversation/session. New messages for a busy session are queued and
processed in FIFO order after the current message completes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class SessionLockManager:
    """Manages per-session locks for sequential message processing.

    Each session gets its own asyncio.Lock. When a message arrives for
    a busy session, it waits for the lock with a configurable timeout.
    Queue position tracking allows clients to show wait status.
    """

    def __init__(self, default_timeout: float = 300.0) -> None:
        """Initialize the lock manager.

        Args:
            default_timeout: Default lock acquisition timeout in seconds (5 minutes).
        """
        self._locks: dict[str, asyncio.Lock] = {}
        self._queue_positions: dict[str, int] = {}
        self._default_timeout = default_timeout

    @asynccontextmanager
    async def acquire(
        self,
        session_id: str,
        timeout: float | None = None,
    ) -> AsyncGenerator[dict[str, int | float], None]:
        """Acquire a lock for the given session.

        Args:
            session_id: The session to lock.
            timeout: Maximum seconds to wait. Uses default if None.

        Yields:
            Dict with queue_position (0=processing, 1+=queued) and
            wait_ms (time spent waiting in milliseconds).

        Raises:
            asyncio.TimeoutError: If lock cannot be acquired within timeout.
        """
        if timeout is None:
            timeout = self._default_timeout

        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()

        lock = self._locks[session_id]

        # Track queue position
        if session_id not in self._queue_positions:
            self._queue_positions[session_id] = 0
        self._queue_positions[session_id] += 1
        position = self._queue_positions[session_id]

        start_time = time.monotonic()
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            wait_ms = (time.monotonic() - start_time) * 1000
            logger.debug(f"Session {session_id}: lock acquired after {wait_ms:.0f}ms (position {position})")
            yield {
                "queue_position": position - 1,
                "wait_ms": round(wait_ms, 1),
            }
        except TimeoutError:
            wait_ms = (time.monotonic() - start_time) * 1000
            logger.warning(f"Session {session_id}: lock timeout after {wait_ms:.0f}ms")
            raise
        finally:
            self._queue_positions[session_id] -= 1
            if self._queue_positions[session_id] <= 0:
                del self._queue_positions[session_id]
            if lock.locked():
                lock.release()

    def get_queue_position(self, session_id: str) -> int:
        """Get the current queue position for a session.

        Args:
            session_id: The session to check.

        Returns:
            0 if idle, 1+ if messages are queued.
        """
        return self._queue_positions.get(session_id, 0)

    def cleanup_session(self, session_id: str) -> None:
        """Remove lock and queue state for a session.

        Args:
            session_id: The session to clean up.
        """
        self._locks.pop(session_id, None)
        self._queue_positions.pop(session_id, None)


session_lock_manager = SessionLockManager()
