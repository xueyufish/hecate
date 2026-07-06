"""Tests for cost budget management service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.model_hub.cost_management import BudgetEnforcementHook, CostBudgetService
from hecate.models.model_cost_budget import ModelCostBudgetCreateSchema


@pytest.fixture
def service(db_session: AsyncSession) -> CostBudgetService:
    return CostBudgetService(db_session)


@pytest.fixture
def hook(db_session: AsyncSession) -> BudgetEnforcementHook:
    return BudgetEnforcementHook(db_session)


async def test_create_budget(service: CostBudgetService) -> None:
    data = ModelCostBudgetCreateSchema(
        scope="workspace",
        limit_amount=100.0,
        period="monthly",
        currency="USD",
        policy="alert",
    )
    budget = await service.create_budget(data)
    assert budget.limit_amount == 100.0
    assert budget.period == "monthly"
    assert budget.policy == "alert"


async def test_list_budgets(service: CostBudgetService) -> None:
    data = ModelCostBudgetCreateSchema(
        scope="workspace",
        limit_amount=100.0,
    )
    await service.create_budget(data)
    budgets = await service.list_budgets()
    assert len(budgets) >= 1


async def test_update_budget(service: CostBudgetService) -> None:
    data = ModelCostBudgetCreateSchema(scope="workspace", limit_amount=100.0)
    budget = await service.create_budget(data)
    from hecate.models.model_cost_budget import ModelCostBudgetUpdateSchema

    updated = await service.update_budget(budget.id, ModelCostBudgetUpdateSchema(limit_amount=200.0))
    assert updated is not None
    assert updated.limit_amount == 200.0


async def test_delete_budget(service: CostBudgetService) -> None:
    data = ModelCostBudgetCreateSchema(scope="workspace", limit_amount=100.0)
    budget = await service.create_budget(data)
    deleted = await service.delete_budget(budget.id)
    assert deleted is True
    result = await service.get_budget_status(budget.id)
    assert result is None


async def test_budget_status(service: CostBudgetService) -> None:
    data = ModelCostBudgetCreateSchema(scope="workspace", limit_amount=100.0)
    budget = await service.create_budget(data)
    status = await service.get_budget_status(budget.id)
    assert status is not None
    assert status.limit_amount == 100.0
    assert status.status_band in ("healthy", "caution", "warning", "critical", "breached")


async def test_enforcement_allows(service: CostBudgetService, hook: BudgetEnforcementHook) -> None:
    ws_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    allowed, reason = await service.check_enforcement(ws_id)
    assert allowed is True
    assert reason == ""


async def test_anomaly_detection_empty(service: CostBudgetService) -> None:
    anomalies = await service.detect_anomalies()
    assert anomalies == []


async def test_forecast_insufficient_data(service: CostBudgetService) -> None:
    forecast = await service.forecast_spend()
    assert forecast.status == "insufficient_data"


async def test_chargeback_empty(service: CostBudgetService) -> None:
    entries = await service.generate_chargeback()
    assert entries == []
