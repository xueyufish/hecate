"""FastAPI dependency provider for feature flag service."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db
from hecate.core.feature_flags import FeatureFlagCache, FeatureFlagService


def get_feature_flag_service(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeatureFlagService:
    """Build the per-request FeatureFlagService.

    The service holds a request-scoped ``AsyncSession`` and a
    process-wide cache singleton (``app.state.feature_flag_cache``,
    wired during lifespan startup). Missing state (e.g. tests without
    the full lifespan) falls back to a no-op cache.
    """
    cache = getattr(request.app.state, "feature_flag_cache", None)
    if cache is None:
        cache = FeatureFlagCache(None)
    return FeatureFlagService(db, cache)
