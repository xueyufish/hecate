"""Feature flag service — CRUD + lifecycle + Redis cache integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.feature_flag import FeatureFlagModel
from hecate.services.feature_flags.evaluator import (
    VALID_STATUSES,
    FeatureFlagEvaluator,
)
from hecate.services.feature_flags.redis_cache import FeatureFlagCache

logger = logging.getLogger(__name__)

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"active", "retired"},
    "active": {"deprecated", "retired"},
    "deprecated": {"active", "retired"},
    "retired": set(),
}


class FeatureFlagService:
    """CRUD + lifecycle for runtime feature flags (Tier 2)."""

    def __init__(self, db: AsyncSession, cache: FeatureFlagCache) -> None:
        self._db = db
        self._cache = cache
        self._evaluator = FeatureFlagEvaluator()

    async def list(self) -> list[FeatureFlagModel]:
        result = await self._db.execute(select(FeatureFlagModel).order_by(FeatureFlagModel.key))
        return list(result.scalars().all())

    async def get(self, key: str) -> FeatureFlagModel | None:
        result = await self._db.execute(select(FeatureFlagModel).where(FeatureFlagModel.key == key))
        return result.scalars().first()

    async def create(self, key: str, description: str | None = None) -> FeatureFlagModel:
        existing = await self.get(key)
        if existing is not None:
            raise ValueError(f"feature flag {key!r} already exists")
        flag = FeatureFlagModel(
            key=key,
            status="draft",
            enabled=False,
            description=description,
        )
        self._db.add(flag)
        await self._db.flush()
        await self._cache.invalidate(key)
        logger.info("feature_flag_created", extra={"key": key})
        return flag

    async def update(
        self,
        key: str,
        *,
        enabled: bool | None = None,
        targeting_rules: dict | None = None,
        description: str | None = None,
        target_removal_version: str | None = None,
    ) -> FeatureFlagModel:
        flag = await self.get(key)
        if flag is None:
            raise KeyError(f"feature flag {key!r} not found")
        if enabled is not None:
            flag.enabled = enabled
        if targeting_rules is not None:
            flag.targeting_rules = targeting_rules
        if description is not None:
            flag.description = description
        if target_removal_version is not None:
            flag.target_removal_version = target_removal_version
        flag.updated_at = datetime.now(UTC)
        await self._db.flush()
        await self._cache.invalidate(key)
        logger.info("feature_flag_updated", extra={"key": key})
        return flag

    async def transition(self, key: str, new_status: str) -> FeatureFlagModel:
        if new_status not in VALID_STATUSES:
            raise ValueError(f"invalid status {new_status!r}")
        flag = await self.get(key)
        if flag is None:
            raise KeyError(f"feature flag {key!r} not found")
        allowed = VALID_TRANSITIONS.get(flag.status, set())
        if new_status not in allowed:
            raise ValueError(f"cannot transition flag {key!r} from {flag.status!r} to {new_status!r}")
        flag.status = new_status
        flag.updated_at = datetime.now(UTC)
        await self._db.flush()
        await self._cache.invalidate(key)
        logger.info("feature_flag_transitioned", extra={"key": key, "status": new_status})
        return flag

    async def delete(self, key: str) -> None:
        flag = await self.get(key)
        if flag is None:
            raise KeyError(f"feature flag {key!r} not found")
        if flag.status != "retired":
            raise ValueError(f"feature flag {key!r} must be retired before deletion")
        await self._db.delete(flag)
        await self._db.flush()
        await self._cache.invalidate(key)
        logger.info("feature_flag_deleted", extra={"key": key})

    async def evaluate(
        self,
        key: str,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        """Evaluate a feature flag with Redis cache (TTL 5s)."""
        cached = await self._cache.get(key)
        if cached is not None:
            return self._evaluator.evaluate(cached, tenant_id=tenant_id, user_id=user_id)

        flag = await self.get(key)
        if flag is None:
            await self._cache.set(key, {"status": "draft", "enabled": False, "key": key})
            return False

        flag_dict: dict[str, Any] = {
            "key": flag.key,
            "status": flag.status,
            "enabled": flag.enabled,
            "targeting_rules": flag.targeting_rules,
        }
        await self._cache.set(key, flag_dict)
        return self._evaluator.evaluate(flag_dict, tenant_id=tenant_id, user_id=user_id)
