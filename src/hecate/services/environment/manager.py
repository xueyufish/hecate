"""Environment Manager — lifecycle management for agent environments.

Provides lazy creation, caching, and TTL-based eviction of agent environments.
"""

from __future__ import annotations

import asyncio
import logging
import time

from hecate.core.config import settings
from hecate.services.environment.environment import AgentEnvironment, LocalEnvironment

logger = logging.getLogger(__name__)


class EnvironmentManager:
    """Manages agent environment lifecycle with lazy creation and TTL eviction.

    Args:
        ttl: Time-to-live in seconds for idle environments. Default: settings.AGENT_ENV_TTL.
        root: Root directory for environments. Default: settings.WORKSPACE_ROOT.
    """

    def __init__(self, ttl: int | None = None, root: str | None = None) -> None:
        self._ttl = ttl or settings.AGENT_ENV_TTL
        self._root = root or settings.WORKSPACE_ROOT
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, agent_id: str) -> AgentEnvironment:
        """Get an existing environment or create a new one (lazy creation).

        Args:
            agent_id: The agent identifier.

        Returns:
            The agent's environment.
        """
        async with self._lock:
            entry = self._cache.get(agent_id)
            if entry is not None:
                if not entry.is_expired(self._ttl):
                    entry.touch()
                    return entry.environment
                # Expired — close and remove
                logger.info("Environment for agent '%s' expired, recreating", agent_id)
                await entry.environment.ensure_dirs()  # ensure dirs still exist
                entry.touch()
                return entry.environment

            # Create new environment
            env = LocalEnvironment(agent_id, self._root)
            await env.ensure_dirs()
            self._cache[agent_id] = _CacheEntry(environment=env)
            logger.info("Created environment for agent '%s'", agent_id)
            return env

    async def close(self, agent_id: str) -> None:
        """Close and remove an environment from cache.

        Args:
            agent_id: The agent identifier.
        """
        async with self._lock:
            entry = self._cache.pop(agent_id, None)
            if entry is not None:
                logger.info("Closed environment for agent '%s'", agent_id)

    async def close_all(self) -> None:
        """Close all cached environments (e.g., on application shutdown)."""
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            if count > 0:
                logger.info("Closed %d environments", count)

    def get_stats(self) -> dict[str, int]:
        """Get manager statistics.

        Returns:
            Dict with cached_count and ttl.
        """
        return {
            "cached_count": len(self._cache),
            "ttl": self._ttl,
        }


class _CacheEntry:
    """Internal cache entry for an environment."""

    def __init__(self, environment: AgentEnvironment) -> None:
        self.environment = environment
        self._last_access = time.monotonic()

    def touch(self) -> None:
        """Reset the last access time."""
        self._last_access = time.monotonic()

    def is_expired(self, ttl: int) -> bool:
        """Check if this entry has exceeded the TTL."""
        return (time.monotonic() - self._last_access) >= ttl
