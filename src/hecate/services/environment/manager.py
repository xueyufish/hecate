"""Environment Manager — lifecycle management for agent environments.

Provides lazy creation, caching, TTL-based eviction, and backend selection
(``local`` / ``docker``) for agent environments. When using the Docker
backend, a warm pool of idle containers is maintained for reuse.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Protocol

from hecate.core.config import settings
from hecate.services.environment.environment import AgentEnvironment, LocalEnvironment

logger = logging.getLogger(__name__)

_VALID_BACKENDS = ("local", "docker")


class _EnvironmentFactory(Protocol):
    def __call__(self, agent_id: str) -> AgentEnvironment: ...


class EnvironmentManager:
    """Manages agent environment lifecycle with lazy creation and TTL eviction.

    Args:
        ttl: Time-to-live in seconds for idle environments. Default: settings.AGENT_ENV_TTL.
        root: Root directory for local environments. Default: settings.WORKSPACE_ROOT.
        backend: Backend type override (``"local"`` or ``"docker"``). Default: settings.AGENT_ENV_BACKEND.
    """

    def __init__(
        self,
        ttl: int | None = None,
        root: str | None = None,
        backend: str | None = None,
    ) -> None:
        self._ttl = ttl or settings.AGENT_ENV_TTL
        self._root = root or settings.WORKSPACE_ROOT
        self._backend = backend or settings.AGENT_ENV_BACKEND

        if self._backend not in _VALID_BACKENDS:
            raise ValueError(
                f"Invalid AGENT_ENV_BACKEND: {self._backend!r}. Valid values: {_VALID_BACKENDS}",
            )

        self._cache: dict[str, _CacheEntry] = {}
        self._warm_pool: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    def _create_environment(self, agent_id: str) -> AgentEnvironment:
        """Create a new environment instance based on the configured backend."""
        if self._backend == "docker":
            from hecate.services.environment.credential_scope import CredentialScope
            from hecate.services.environment.docker_environment import DockerEnvironment
            from hecate.services.environment.network_policy import (
                NetworkEgressPolicy,
                NetworkPolicyMode,
            )

            network_policy = NetworkEgressPolicy(
                mode=NetworkPolicyMode(settings.AGENT_ENV_NETWORK_POLICY),
            )
            credential_scope = CredentialScope(
                enabled=settings.AGENT_ENV_CREDENTIAL_SCOPING,
            )
            return DockerEnvironment(
                agent_id,
                network_policy=network_policy,
                credential_scope=credential_scope,
            )

        if settings.AGENT_ENV_NETWORK_POLICY == "deny_all":
            logger.warning(
                "Network egress control not available on LocalEnvironment (AGENT_ENV_NETWORK_POLICY=deny_all ignored)",
            )
        if settings.AGENT_ENV_CREDENTIAL_SCOPING:
            logger.warning(
                "Credential scoping not available on LocalEnvironment (AGENT_ENV_CREDENTIAL_SCOPING ignored)",
            )

        return LocalEnvironment(agent_id, self._root)

    @staticmethod
    def _compute_security_config_hash() -> str:
        """Compute a hash of the effective security configuration.

        When this hash changes, warm pool containers are invalidated
        to ensure they pick up the new security settings.
        """
        parts = [
            settings.AGENT_ENV_NETWORK_POLICY,
            str(settings.AGENT_ENV_CREDENTIAL_SCOPING),
            str(settings.AGENT_ENV_SANDBOX_ENFORCEMENT),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def invalidate_agent(self, agent_id: str) -> None:
        """Invalidate cached and warm pool entries for an agent.

        Called when an agent's security configuration changes.
        Destroys warm pool containers so the next get_or_create()
        creates a fresh container with updated config.
        """
        self._cache.pop(agent_id, None)
        self._warm_pool.pop(agent_id, None)
        logger.info("Invalidated environments for agent '%s'", agent_id)

    async def get_or_create(self, agent_id: str) -> AgentEnvironment:
        """Get an existing environment or create a new one (lazy creation).

        Args:
            agent_id: The agent identifier.

        Returns:
            The agent's environment.
        """
        async with self._lock:
            await self._sweep_warm_pool()

            entry = self._cache.get(agent_id)
            if entry is not None:
                if not entry.is_expired(self._ttl):
                    entry.touch()
                    return entry.environment
                logger.info("Environment for agent '%s' expired, recreating", agent_id)
                await entry.environment.ensure_dirs()
                entry.touch()
                return entry.environment

            warm_entry = self._warm_pool.pop(agent_id, None)
            if warm_entry is not None:
                current_hash = self._compute_security_config_hash()
                if warm_entry.security_config_hash != current_hash:
                    logger.info(
                        "Security config changed for agent '%s', destroying stale warm container",
                        agent_id,
                    )
                    if self._backend == "docker":
                        from hecate.services.environment.docker_environment import (
                            DockerEnvironment,
                        )

                        if isinstance(warm_entry.environment, DockerEnvironment):
                            await warm_entry.environment.remove()
                else:
                    logger.info("Reusing warm container for agent '%s'", agent_id)
                    env = warm_entry.environment
                    if self._backend == "docker":
                        from hecate.services.environment.docker_environment import (
                            DockerEnvironment,
                        )

                        if isinstance(env, DockerEnvironment):
                            await env.start()
                    await env.ensure_dirs()
                    entry = _CacheEntry(environment=env)
                    entry.security_config_hash = current_hash
                    self._cache[agent_id] = entry
                    return env

            env = self._create_environment(agent_id)
            await env.ensure_dirs()
            entry = _CacheEntry(environment=env)
            entry.security_config_hash = self._compute_security_config_hash()
            self._cache[agent_id] = entry
            logger.info("Created %s environment for agent '%s'", self._backend, agent_id)
            return env

    async def close(self, agent_id: str) -> None:
        """Close an environment and move it to the warm pool (Docker) or evict (local).

        Args:
            agent_id: The agent identifier.
        """
        async with self._lock:
            entry = self._cache.pop(agent_id, None)
            if entry is None:
                return

            if self._backend == "docker" and len(self._warm_pool) < settings.DOCKER_WARM_POOL_SIZE:
                from hecate.services.environment.docker_environment import DockerEnvironment

                if isinstance(entry.environment, DockerEnvironment):
                    await entry.environment.stop()
                self._warm_pool[agent_id] = entry
                logger.info("Moved environment '%s' to warm pool", agent_id)
            else:
                if self._backend == "docker":
                    from hecate.services.environment.docker_environment import DockerEnvironment

                    if isinstance(entry.environment, DockerEnvironment):
                        await entry.environment.remove()
                logger.info("Closed environment for agent '%s'", agent_id)

    async def close_all(self) -> None:
        """Close all cached environments and drain the warm pool."""
        async with self._lock:
            count = len(self._cache) + len(self._warm_pool)
            if self._backend == "docker":
                from hecate.services.environment.docker_environment import DockerEnvironment

                for _agent_id, entry in list(self._cache.items()):
                    if isinstance(entry.environment, DockerEnvironment):
                        await entry.environment.remove()
                for _agent_id, entry in list(self._warm_pool.items()):
                    if isinstance(entry.environment, DockerEnvironment):
                        await entry.environment.remove()

            self._cache.clear()
            self._warm_pool.clear()
            if count > 0:
                logger.info("Closed %d environments", count)

    async def _sweep_warm_pool(self) -> None:
        """Evict warm pool entries that have exceeded the idle timeout."""
        if not self._warm_pool:
            return

        idle_timeout = settings.DOCKER_WARM_POOL_IDLE_TIMEOUT
        now = time.monotonic()
        expired = [
            agent_id for agent_id, entry in self._warm_pool.items() if (now - entry._last_access) >= idle_timeout
        ]

        for agent_id in expired:
            entry = self._warm_pool.pop(agent_id, None)
            if entry is not None and self._backend == "docker":
                from hecate.services.environment.docker_environment import DockerEnvironment

                if isinstance(entry.environment, DockerEnvironment):
                    await entry.environment.remove()
                logger.info("Evicted expired warm container for agent '%s'", agent_id)

    def get_stats(self) -> dict[str, int]:
        """Get manager statistics.

        Returns:
            Dict with cached_count, warm_pool_count, and ttl.
        """
        return {
            "cached_count": len(self._cache),
            "warm_pool_count": len(self._warm_pool),
            "ttl": self._ttl,
        }


class _CacheEntry:
    """Internal cache entry for an environment."""

    def __init__(self, environment: AgentEnvironment) -> None:
        self.environment = environment
        self._last_access = time.monotonic()
        self.security_config_hash: str = ""

    def touch(self) -> None:
        """Reset the last access time."""
        self._last_access = time.monotonic()

    def is_expired(self, ttl: int) -> bool:
        """Check if this entry has exceeded the TTL."""
        return (time.monotonic() - self._last_access) >= ttl
