"""Redis cache for feature flags.

TTL is short (default 5s) so that flag state changes via REST API
propagate quickly. The REST API also explicitly invalidates the cache
key on writes (DEL) for immediate propagation.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

CACHE_TTL_SECONDS = 5
KEY_PREFIX = "feature_flag:"


class FeatureFlagCache:
    """Thin async wrapper around Redis for feature flag state caching."""

    def __init__(self, redis_client: Redis | None) -> None:
        self._redis = redis_client

    def _key(self, flag_key: str) -> str:
        return f"{KEY_PREFIX}{flag_key}"

    async def get(self, flag_key: str) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(self._key(flag_key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def set(self, flag_key: str, value: dict[str, Any]) -> None:
        if self._redis is None:
            return
        await self._redis.set(
            self._key(flag_key),
            json.dumps(value, default=str),
            ex=CACHE_TTL_SECONDS,
        )

    async def invalidate(self, flag_key: str) -> None:
        if self._redis is None:
            return
        await self._redis.delete(self._key(flag_key))
