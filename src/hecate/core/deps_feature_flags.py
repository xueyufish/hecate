"""FastAPI dependency provider for feature flag service."""

from __future__ import annotations

from fastapi import Request

from hecate.core.feature_flags import FeatureFlagService


def get_feature_flag_service(request: Request) -> FeatureFlagService:
    """Get the singleton FeatureFlagService from app.state."""
    return request.app.state.feature_flag_service
