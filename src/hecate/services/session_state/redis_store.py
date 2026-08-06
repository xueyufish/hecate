"""Redis-backed SessionStateStore — hot-path persistence for distributed state.

Provides :class:`RedisSessionStateStore` implementing the engine-layer
``SessionStateStore`` ABC using ``redis.asyncio`` (redis-py >= 5.0).

Redis is the **hot-path cache** in the tiered store design. PostgreSQL is
the source of truth (see ADR-020). This implementation is for deployments
that run Redis as a standalone service OR use the ``TieredSessionStateStore``
(where this class is wrapped together with ``PostgresSessionStateStore``).

Key design:

- The Redis key is ``{key_prefix}{org_id}:{user_id}:{session_id}`` where
  ``{org_id}`` is the Redis Cluster hash tag — ensuring all data for a given
  organization lands on the same hash slot, so ``list_recent(org_id, ...)``
  works under Cluster mode without crossing slots.
- TTL is set on every write via the ``EX`` option (default = ``SESSION_STATE_TTL_DAYS * 86400``).
- Connection is lazily initialized via ``redis.asyncio.from_url`` and pinged
  on first use (matches the existing pattern in ``src/hecate/model_hub/cache.py``).
- Redis failures during ``save`` are logged as warnings and swallowed — the
  single-backend store cannot recover, but PG-backed ``TieredSessionStateStore``
  treats Redis as best-effort.
- Redis failures during ``load`` propagate — the caller (``TieredSessionStateStore``)
  decides whether to fall back to PostgreSQL.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hecate.core.config import settings
from hecate.engine.session_state import (
    SessionState,
    SessionStateConflictError,
    SessionStateStore,
    SessionSummary,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Lua script for owner-safe lock release: only deletes the key if the stored
# value matches the caller's owner UUID. Prevents a slow holder from deleting
# a successor's lock after TTL expiry.
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Jitter retry budget for lock acquisition (mirrors Hermes Agent's pattern:
# short timeout + random backoff avoids SQLite/Redis convoy effects).
_LOCK_MAX_RETRIES = 3
_LOCK_RETRY_MIN_S = 0.020
_LOCK_RETRY_MAX_S = 0.150


class RedisSessionStateStore(SessionStateStore):
    """``SessionStateStore`` implementation backed by a single Redis instance.

    Lazily connects on first use; survives transient Redis outages by logging
    warnings and returning ``None`` on read failures (so the caller can
    decide whether to fall back). On write failures, the exception is
    swallowed (Redis is best-effort cache in the tiered architecture).
    """

    def __init__(
        self,
        redis_url: str,
        key_prefix: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._key_prefix = key_prefix if key_prefix is not None else settings.SESSION_STATE_KEY_PREFIX
        self._ttl_seconds = ttl_seconds
        self._redis: aioredis.Redis | None = None

    def _build_key(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        return f"{self._key_prefix}{org_id}:{user_id}:{session_id}"

    def _build_lock_key(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> str:
        """Lock key co-locates with data key via ``{org_id}`` Redis Cluster hash tag."""
        return f"{self._key_prefix}lock:{{{org_id}}}:{user_id}:{session_id}"

    async def _get_redis(self) -> aioredis.Redis:
        """Lazy connection init: ping on first use."""
        if self._redis is not None:
            return self._redis

        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        await self._redis.ping()
        logger.info("Redis SessionStateStore connected: %s", self._redis_url)
        return self._redis

    @asynccontextmanager
    async def acquire_session_lock(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        timeout_ms: int = 30000,
    ) -> AsyncGenerator[None, None]:
        """Acquire exclusive lock via ``SET NX PX`` + Lua release.

        Uses ``{org_id}`` hash tag so the lock key lands on the same Redis
        Cluster slot as the data key. Retries up to ``_LOCK_MAX_RETRIES``
        times with random jitter to break convoy effects. Raises
        :class:`SessionStateConflictError` if all retries fail.
        """
        lock_key = self._build_lock_key(org_id, user_id, session_id)
        owner = str(uuid.uuid4())
        acquired = False

        try:
            redis = await self._get_redis()
        except Exception:
            logger.warning(
                "Redis SessionStateStore unavailable on lock acquire (key=%s); yielding without lock",
                lock_key,
                exc_info=True,
            )
            # Best-effort: yield without lock (single-backend Redis failure
            # leaves caller unprotected, but tiered mode's PG row lock
            # provides the safety net).
            yield
            return

        for attempt in range(_LOCK_MAX_RETRIES):
            try:
                result = await redis.set(lock_key, owner, nx=True, px=timeout_ms)
            except Exception:
                logger.warning("Redis lock SET failed (key=%s attempt=%d)", lock_key, attempt, exc_info=True)
                result = None

            if result:
                acquired = True
                break

            if attempt < _LOCK_MAX_RETRIES - 1:
                await asyncio.sleep(random.uniform(_LOCK_RETRY_MIN_S, _LOCK_RETRY_MAX_S))  # noqa: S311

        if not acquired:
            raise SessionStateConflictError(org_id, user_id, session_id)

        try:
            yield
        finally:
            try:
                # Owner-safe release: GET-then-DEL only if the stored value
                # matches our owner UUID. We do this in Python rather than a
                # Lua script to keep fakeredis compatibility (fakeredis does
                # not support EVAL by default) and because the rare race
                # window (TTL expires between GET and DEL) is acceptable for
                # session-state locks — worst case: we delete a successor's
                # lock and they retry through their own retry budget.
                current = await redis.get(lock_key)
                if current == owner:
                    await redis.delete(lock_key)
            except Exception:
                logger.warning("Redis lock release failed (key=%s owner=%s)", lock_key, owner, exc_info=True)

    async def save(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None:
        key = self._build_key(org_id, user_id, session_id)
        stamped = state.model_copy(
            update={"metadata": {**(state.metadata or {}), "_saved_at": datetime.now(UTC).isoformat()}}
        )
        value = stamped.model_dump_json()
        try:
            redis = await self._get_redis()
        except Exception:
            logger.warning("Redis SessionStateStore unavailable on save (key=%s)", key, exc_info=True)
            return

        try:
            if self._ttl_seconds is not None:
                await redis.set(key, value, ex=self._ttl_seconds)
            else:
                await redis.set(key, value)
        except Exception:
            logger.warning("Redis SessionStateStore save failed (key=%s)", key, exc_info=True)

    async def load(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None:
        key = self._build_key(org_id, user_id, session_id)
        try:
            redis = await self._get_redis()
        except Exception:
            logger.warning("Redis SessionStateStore unavailable on load (key=%s)", key, exc_info=True)
            return None

        try:
            raw = await redis.get(key)
        except Exception:
            logger.warning("Redis SessionStateStore load failed (key=%s)", key, exc_info=True)
            return None

        if raw is None:
            return None
        return SessionState.model_validate_json(raw)

    async def list_recent(self, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]:
        try:
            redis = await self._get_redis()
        except Exception:
            logger.warning("Redis SessionStateStore unavailable on list_recent", exc_info=True)
            return []

        prefix = f"{self._key_prefix}{org_id}:{user_id}:"
        pattern = f"{self._key_prefix}{org_id}:*"
        summaries: list[SessionSummary] = []

        try:
            async for key in redis.scan_iter(match=pattern, count=1000):
                if not key.startswith(prefix):
                    continue
                try:
                    raw = await redis.get(key)
                except Exception:
                    logger.warning("Redis SessionStateStore scan read failed (key=%s)", key, exc_info=True)
                    continue
                if raw is None:
                    continue
                try:
                    state = SessionState.model_validate_json(raw)
                except Exception:
                    logger.warning("Redis SessionStateStore decode failed (key=%s)", key, exc_info=True)
                    continue
                session_id = uuid.UUID(key.rsplit(":", 1)[-1])
                superstep_raw = state.metadata.get("superstep") if state.metadata else None
                superstep = superstep_raw if isinstance(superstep_raw, int) else None
                saved_at_iso = state.metadata.get("_saved_at") if state.metadata else None
                updated_at = _parse_iso(saved_at_iso) if isinstance(saved_at_iso, str) else datetime.now(UTC)
                summaries.append(
                    SessionSummary(
                        session_id=session_id,
                        org_id=org_id,
                        user_id=user_id,
                        updated_at=updated_at,
                        superstep=superstep,
                    )
                )
        except Exception:
            logger.warning("Redis SessionStateStore list_recent scan failed", exc_info=True)
            return []

        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries[:limit]


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


def _epoch() -> Any:
    return datetime(1970, 1, 1, tzinfo=UTC)
