"""Cache strategy ABC and implementations for intelligent routing.

Provides CacheStrategyABC for pluggable caching with InMemoryCacheStrategy
as default and optional RedisCacheStrategy.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class CacheStrategyABC(abc.ABC):
    """Abstract base class for cache strategies.

    Implementations must provide get, set, invalidate, and stats methods.
    """

    @abc.abstractmethod
    async def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a cached value by key.

        Args:
            key: Cache key.

        Returns:
            Cached value or None if not found/expired.
        """

    @abc.abstractmethod
    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        """Store a value in the cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl: Time-to-live in seconds.
        """

    @abc.abstractmethod
    async def invalidate(self, pattern: str) -> int:
        """Invalidate cached entries matching a pattern.

        Args:
            pattern: Glob-style pattern (e.g., "gpt-4o:*").

        Returns:
            Number of entries invalidated.
        """

    @abc.abstractmethod
    async def stats(self) -> dict[str, Any]:
        """Return cache statistics.

        Returns:
            Dict with hits, misses, size, hit_rate.
        """


class InMemoryCacheStrategy(CacheStrategyABC):
    """In-memory cache with TTL-based expiry.

    Uses a dict with entry metadata for TTL tracking.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._hits: int = 0
        self._misses: int = 0

    async def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a cached value by key."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._cache[key]
            self._misses += 1
            return None

        self._hits += 1
        return value

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        """Store a value in the cache."""
        self._cache[key] = (time.monotonic() + ttl, value)

    async def invalidate(self, pattern: str) -> int:
        """Invalidate cached entries matching a pattern."""
        import fnmatch

        keys_to_remove = [k for k in self._cache if fnmatch.fnmatch(k, pattern)]
        for key in keys_to_remove:
            del self._cache[key]
        return len(keys_to_remove)

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "hit_rate": self._hits / total if total > 0 else 0.0,
        }

    async def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0


class RedisCacheStrategy(CacheStrategyABC):
    """Redis-backed cache strategy.

    Requires the `redis` package and a configured Redis URL.
    Falls back to InMemoryCacheStrategy if Redis is unavailable.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None
        self._fallback = InMemoryCacheStrategy()
        self._hits: int = 0
        self._misses: int = 0

    async def _get_redis(self) -> Any:
        """Lazy Redis connection."""
        if self._redis is not None:
            return self._redis

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            logger.info("Redis cache connected: %s", self._redis_url)
            return self._redis
        except Exception:
            logger.warning("Redis unavailable, falling back to in-memory cache")
            return None

    async def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a cached value by key."""
        r = await self._get_redis()
        if r is None:
            return await self._fallback.get(key)

        try:
            value = await r.get(f"router:{key}")
            if value is None:
                self._misses += 1
                return None
            self._hits += 1
            return json.loads(value)
        except Exception:
            self._misses += 1
            return None

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        """Store a value in the cache."""
        r = await self._get_redis()
        if r is None:
            await self._fallback.set(key, value, ttl)
            return

        try:
            await r.set(f"router:{key}", json.dumps(value), ex=ttl)
        except Exception:
            await self._fallback.set(key, value, ttl)

    async def invalidate(self, pattern: str) -> int:
        """Invalidate cached entries matching a pattern."""
        r = await self._get_redis()
        if r is None:
            return await self._fallback.invalidate(pattern)

        try:
            count = 0
            async for key in r.scan_iter(match=f"router:{pattern}"):
                await r.delete(key)
                count += 1
            return count
        except Exception:
            return await self._fallback.invalidate(pattern)

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        r = await self._get_redis()
        if r is None:
            return await self._fallback.stats()

        try:
            info = await r.info("stats")
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": info.get("keyspace_hits", 0),
                "hit_rate": self._hits / total if total > 0 else 0.0,
                "backend": "redis",
            }
        except Exception:
            return await self._fallback.stats()


def generate_cache_key(model: str, messages: list[dict], temperature: float = 0.7) -> str:
    """Generate deterministic cache key from model invocation parameters.

    Args:
        model: Model identifier.
        messages: Conversation messages.
        temperature: Sampling temperature.

    Returns:
        SHA-256 hash with model prefix.
    """
    content = json.dumps({"messages": messages, "temperature": temperature}, sort_keys=True)
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"{model}:{content_hash}"
