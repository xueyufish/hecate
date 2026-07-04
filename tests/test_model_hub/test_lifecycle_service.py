"""Tests for LifecycleService — promotion, approval, deprecation, rollback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.model_hub.lifecycle_service import LifecycleService


class TestLifecycleService:
    async def test_promote_no_source_raises(self, db_session: AsyncSession) -> None:
        service = LifecycleService(db_session)
        with pytest.raises(ValueError, match="No deployment found"):
            await service.promote("nonexistent", "dev", "staging")

    async def test_approve_not_found_raises(self, db_session: AsyncSession) -> None:
        service = LifecycleService(db_session)
        with pytest.raises(ValueError, match="not found"):
            await service.approve(uuid.uuid4(), uuid.uuid4())

    async def test_deprecate_no_prod_raises(self, db_session: AsyncSession) -> None:
        service = LifecycleService(db_session)
        with pytest.raises(ValueError, match="No prod deployment"):
            await service.deprecate("nonexistent", datetime.now(UTC) + timedelta(days=30))

    async def test_list_deployments_empty(self, db_session: AsyncSession) -> None:
        service = LifecycleService(db_session)
        result = await service.list_deployments()
        assert result == []
