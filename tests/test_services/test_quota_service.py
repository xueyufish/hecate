"""Tests for quota management: models, service CRUD, usage tracking, enforcement."""

from __future__ import annotations

import uuid
import uuid as _uuid
from datetime import UTC, datetime, timedelta

import pytest

from hecate.models.quota import (
    EnforcementMode,
    QuotaModel,
    QuotaResourceType,
    QuotaScope,
    QuotaUsageModel,
    QuotaWindowType,
)
from hecate.services.quota_service import QuotaService, _invalidate_cache

_DEFAULT_WS = _uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture(autouse=True)
def _clear_quota_cache():
    """Clear the module-level quota cache between tests."""
    _invalidate_cache(_DEFAULT_WS)
    yield
    _invalidate_cache(_DEFAULT_WS)


class TestQuotaModels:
    """Test ORM model creation."""

    async def test_create_quota_model(self, db_session):
        quota = QuotaModel(
            name="Monthly Cost Cap",
            resource_type=QuotaResourceType.COST,
            scope=QuotaScope.WORKSPACE,
            scope_id=uuid.uuid4(),
            limit_value=1000.0,
            soft_limit=800.0,
            window_type=QuotaWindowType.MONTHLY,
        )
        db_session.add(quota)
        await db_session.flush()
        assert quota.id is not None
        assert quota.enforcement == "hard_reject"
        assert quota.enabled is True

    async def test_create_usage_model(self, db_session):
        quota_id = uuid.uuid4()
        now = datetime.now(UTC)
        usage = QuotaUsageModel(
            quota_id=quota_id,
            period_start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            period_end=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=30),
            used_value=42.5,
        )
        db_session.add(usage)
        await db_session.flush()
        assert usage.id is not None
        assert usage.used_value == 42.5
        assert usage.soft_limit_triggered is False


class TestQuotaServiceCRUD:
    """Test QuotaService CRUD operations."""

    async def test_quota_crud(self, db_session):
        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        quota = await service.create_quota(
            name="Daily Token Limit",
            resource_type=QuotaResourceType.TOKENS,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=500000,
            window_type=QuotaWindowType.DAILY,
        )
        assert quota.id is not None

        quotas = await service.list_quotas()
        assert len(quotas) == 1

        fetched = await service.get_quota(quota.id)
        assert fetched.name == "Daily Token Limit"

        updated = await service.update_quota(quota.id, limit_value=600000)
        assert updated.limit_value == 600000

        deleted = await service.delete_quota(quota.id)
        assert deleted is True

    async def test_list_filter_by_resource_type(self, db_session):
        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        await service.create_quota(
            name="Cost",
            resource_type=QuotaResourceType.COST,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=100,
            window_type=QuotaWindowType.MONTHLY,
        )
        await service.create_quota(
            name="Tokens",
            resource_type=QuotaResourceType.TOKENS,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=100000,
            window_type=QuotaWindowType.DAILY,
        )
        cost_only = await service.list_quotas(resource_type="cost")
        assert len(cost_only) == 1
        assert cost_only[0].name == "Cost"


class TestQuotaUsageTracking:
    """Test usage recording and checking."""

    async def test_record_and_check(self, db_session):
        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        await service.create_quota(
            name="Daily Requests",
            resource_type=QuotaResourceType.REQUESTS,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=100,
            window_type=QuotaWindowType.DAILY,
        )

        allowed, remaining, _ = await service.check_quota("requests", "workspace", scope_id, "daily")
        assert allowed is True
        assert remaining == 100

        await service.record_usage("requests", "workspace", scope_id, "daily", 30)
        allowed, remaining, _ = await service.check_quota("requests", "workspace", scope_id, "daily")
        assert allowed is True
        assert remaining == 70

        await service.record_usage("requests", "workspace", scope_id, "daily", 70)
        allowed, remaining, _ = await service.check_quota("requests", "workspace", scope_id, "daily")
        assert allowed is False
        assert remaining == 0

    async def test_soft_allow_enforcement(self, db_session):
        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        await service.create_quota(
            name="Soft Cost",
            resource_type=QuotaResourceType.COST,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=100.0,
            window_type=QuotaWindowType.MONTHLY,
            enforcement=EnforcementMode.SOFT_ALLOW,
        )

        await service.record_usage("cost", "workspace", scope_id, "monthly", 150.0)
        allowed, remaining, _ = await service.check_quota("cost", "workspace", scope_id, "monthly")
        assert allowed is True
        assert remaining == 0

    async def test_soft_limit_alert(self, db_session):
        from hecate.models.alert import AlertState
        from hecate.services.alert_service import AlertService

        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        await service.create_quota(
            name="Cost with Soft Limit",
            resource_type=QuotaResourceType.COST,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=100.0,
            soft_limit=80.0,
            window_type=QuotaWindowType.MONTHLY,
        )

        await service.record_usage("cost", "workspace", scope_id, "monthly", 50.0)
        alert_service = AlertService(db_session)
        events = await alert_service.list_events()
        assert len(events) == 0

        await service.record_usage("cost", "workspace", scope_id, "monthly", 40.0)
        events = await alert_service.list_events()
        assert len(events) == 1
        assert events[0].state == AlertState.FIRING

        await service.record_usage("cost", "workspace", scope_id, "monthly", 10.0)
        events = await alert_service.list_events()
        assert len(events) == 1

    async def test_reset_quota(self, db_session):
        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        quota = await service.create_quota(
            name="Resettable",
            resource_type=QuotaResourceType.REQUESTS,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=100,
            window_type=QuotaWindowType.DAILY,
        )
        await service.record_usage("requests", "workspace", scope_id, "daily", 50)
        usage_list = await service.list_usage()
        assert usage_list[0]["used_value"] == 50

        await service.reset_quota(quota.id)
        usage_list = await service.list_usage()
        assert usage_list[0]["used_value"] == 0

    async def test_usage_list_with_utilization(self, db_session):
        service = QuotaService(db_session)
        scope_id = uuid.uuid4()
        await service.create_quota(
            name="Util Test",
            resource_type=QuotaResourceType.TOKENS,
            scope=QuotaScope.WORKSPACE,
            scope_id=scope_id,
            limit_value=1000,
            window_type=QuotaWindowType.DAILY,
        )
        await service.record_usage("tokens", "workspace", scope_id, "daily", 250)
        usage = await service.list_usage()
        assert len(usage) == 1
        assert usage[0]["used_value"] == 250
        assert usage[0]["remaining"] == 750
        assert usage[0]["utilization_pct"] == 25.0

    async def test_no_quota_allows_all(self, db_session):
        service = QuotaService(db_session)
        allowed, remaining, reset_at = await service.check_quota("requests", "workspace", uuid.uuid4(), "daily")
        assert allowed is True
        assert remaining == float("inf")
        assert reset_at is None
