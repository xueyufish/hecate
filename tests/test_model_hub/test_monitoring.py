"""Tests for monitoring service."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.model_hub.monitoring import MonitoringService


@pytest.fixture
def service(db_session: AsyncSession) -> MonitoringService:
    return MonitoringService(db_session)


async def test_model_performance_empty(service: MonitoringService) -> None:
    result = await service.get_model_performance("nonexistent-model")
    assert result["model_id"] == "nonexistent-model"
    assert result["timeseries"] == []


async def test_compare_models_empty(service: MonitoringService) -> None:
    result = await service.compare_models(["model-a", "model-b"])
    assert len(result) == 2
    assert result[0]["model_id"] == "model-a"
    assert result[0]["request_count"] == 0


async def test_detect_drift_insufficient_data(service: MonitoringService) -> None:
    result = await service.detect_drift("nonexistent-model")
    assert result["drift_detected"] is False
    assert result["reason"] == "insufficient_data"


async def test_cost_trends_empty(service: MonitoringService) -> None:
    result = await service.get_cost_trends_by_model()
    assert result == []


async def test_compute_metrics_empty(service: MonitoringService) -> None:
    metrics = service._compute_metrics([])
    assert metrics["avg_latency"] == 0
    assert metrics["request_count"] == 0
