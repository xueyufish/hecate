"""Tiered SessionStateStore — write-through Redis + PostgreSQL composition.

Provides :class:`TieredSessionStateStore` implementing the engine-layer
``SessionStateStore`` ABC by composing a ``RedisSessionStateStore`` (hot-path
cache) and a ``PostgresSessionStateStore`` (durable truth) per ADR-020 and
the design document for the ``session-state-store-redis-pg`` change.

Coordination protocols:

- **Write-through** — ``save`` persists to PostgreSQL first (source of
  truth), then fills the Redis hot cache. PostgreSQL failure propagates
  as a real error and invalidates any cached copy, so a later ``load``
  falls back to the authoritative store instead of serving state whose
  durable write failed. Redis cache-fill failure is logged and swallowed
  (Redis is best-effort).
- **Read-through** — ``load`` attempts Redis first; on hit, returns
  immediately. On miss or Redis failure, falls back to PostgreSQL; if PG
  returns a state, warms the Redis cache by writing it back. PG failure
  propagates as a real error.
- **List-through** — ``list_recent`` attempts Redis first; on hit, returns
  the Redis-enumerated list. On Redis failure, falls back to PostgreSQL's
  indexed scan. PG failure propagates as a real error.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from hecate.runtime.session_state import SessionState, SessionStateStore, SessionSummary

logger = logging.getLogger(__name__)


class TieredSessionStateStore(SessionStateStore):
    """Composite ``SessionStateStore`` pairing Redis (cache) and PostgreSQL (truth)."""

    def __init__(
        self,
        redis_store: SessionStateStore,
        postgres_store: SessionStateStore,
    ) -> None:
        self._redis = redis_store
        self._postgres = postgres_store

    async def save(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None:
        # PG first (source of truth). Writing Redis first left a window
        # where the durable write failed but subsequent loads kept serving
        # the never-persisted state from cache until TTL expiry.
        try:
            await self._postgres.save(org_id, user_id, session_id, state)
        except Exception:
            # Drop any cached copy so reads fall back to the authoritative
            # store; a failure to invalidate must not mask the original error.
            with contextlib.suppress(Exception):
                await self._redis.invalidate(org_id, user_id, session_id)
            raise
        try:
            await self._redis.save(org_id, user_id, session_id, state)
        except Exception:
            logger.warning(
                "TieredSessionStateStore Redis cache fill failed "
                "(org=%s user=%s session=%s); PG holds the authoritative copy",
                org_id,
                user_id,
                session_id,
                exc_info=True,
            )

    async def load(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None:
        try:
            state = await self._redis.load(org_id, user_id, session_id)
            if state is not None:
                return state
        except Exception:
            logger.warning(
                "TieredSessionStateStore Redis load failed (org=%s user=%s session=%s); falling back to PG",
                org_id,
                user_id,
                session_id,
                exc_info=True,
            )

        state = await self._postgres.load(org_id, user_id, session_id)
        if state is not None:
            try:
                await self._redis.save(org_id, user_id, session_id, state)
            except Exception:
                logger.warning(
                    "TieredSessionStateStore Redis cache-warm failed (org=%s user=%s session=%s)",
                    org_id,
                    user_id,
                    session_id,
                    exc_info=True,
                )
        return state

    async def list_recent(self, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]:
        try:
            summaries = await self._redis.list_recent(org_id, user_id, limit)
            if summaries:
                return summaries
        except Exception:
            logger.warning(
                "TieredSessionStateStore Redis list_recent failed (org=%s user=%s); falling back to PG",
                org_id,
                user_id,
                exc_info=True,
            )

        return await self._postgres.list_recent(org_id, user_id, limit)

    @asynccontextmanager
    async def acquire_session_lock(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        timeout_ms: int = 30000,
    ) -> AsyncGenerator[None, None]:
        """Delegate lock to Redis (primary); PG row lock on save provides backstop.

        If Redis is unavailable, swallow the exception and yield without the
        Redis lock — the subsequent ``save`` call's PG ``SELECT FOR UPDATE``
        row lock still serializes concurrent writers on the source-of-truth
        tier.
        """
        try:
            async with self._redis.acquire_session_lock(org_id, user_id, session_id, timeout_ms=timeout_ms):
                yield
        except Exception as redis_exc:
            # Distinguish Redis-failure-swallow from genuine SessionStateConflictError
            # (which the Redis lock raises after exhausting its retry budget).
            from hecate.runtime.session_state import SessionStateConflictError

            if isinstance(redis_exc, SessionStateConflictError):
                raise
            logger.warning(
                "TieredSessionStateStore Redis lock unavailable (org=%s user=%s session=%s); "
                "yielding with PG-row-lock backstop only",
                org_id,
                user_id,
                session_id,
                exc_info=True,
            )
            yield
