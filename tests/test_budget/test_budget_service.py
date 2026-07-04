"""Tests for BudgetService — utilization, forecast, chargeback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from hecate.budget.budget_service import BudgetService


class TestBudgetService:
    async def test_get_utilization_no_quota(self, db_session) -> None:
        service = BudgetService(db_session)
        result = await service.get_utilization("workspace", uuid.uuid4())
        assert result["spent"] == 0.0
        assert result["utilization_pct"] == 0.0

    async def test_forecast_remaining(self, db_session) -> None:
        service = BudgetService(db_session)
        result = await service.forecast_remaining("workspace", uuid.uuid4())
        assert "current_spend" in result
        assert "avg_daily_cost" in result
        assert "projected_total" in result
        assert "will_exceed" in result

    async def test_create_chargeback_empty(self, db_session) -> None:
        service = BudgetService(db_session)
        result = await service.create_chargeback(
            scope="workspace",
            scope_id=uuid.uuid4(),
            group_by="model",
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            end_date=datetime(2026, 1, 31, tzinfo=UTC),
        )
        assert isinstance(result, list)
