"""Tests for ModelPricingModel and CostService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.model_pricing import (
    ModelPricingCreateSchema,
    ModelPricingModel,
    ModelPricingReadSchema,
)
from hecate.models.trace import TraceModel
from hecate.services.cost_service import CostService


class TestModelPricingModel:
    """Tests for ModelPricingModel ORM and schemas."""

    async def test_create_with_all_fields(self, db_session: AsyncSession) -> None:
        pricing = ModelPricingModel(
            model_id="gpt-4o",
            display_name="GPT-4o",
            input_price_per_1k=0.0025,
            output_price_per_1k=0.01,
            effective_from=datetime.now(UTC),
        )
        db_session.add(pricing)
        await db_session.flush()
        assert pricing.model_id == "gpt-4o"
        assert pricing.currency == "USD"
        assert pricing.effective_until is None

    async def test_default_currency(self, db_session: AsyncSession) -> None:
        pricing = ModelPricingModel(
            model_id="test-model",
            display_name="Test",
            input_price_per_1k=0.001,
            output_price_per_1k=0.002,
            effective_from=datetime.now(UTC),
        )
        db_session.add(pricing)
        await db_session.flush()
        assert pricing.currency == "USD"

    async def test_read_schema_from_attributes(self, db_session: AsyncSession) -> None:
        pricing = ModelPricingModel(
            model_id="claude-3.5-sonnet",
            display_name="Claude 3.5 Sonnet",
            input_price_per_1k=0.003,
            output_price_per_1k=0.015,
            effective_from=datetime.now(UTC),
        )
        db_session.add(pricing)
        await db_session.flush()
        schema = ModelPricingReadSchema.model_validate(pricing)
        assert schema.model_id == "claude-3.5-sonnet"
        assert schema.input_price_per_1k == 0.003

    def test_create_schema_negative_price_rejected(self) -> None:
        with pytest.raises(ValueError):
            ModelPricingCreateSchema(
                model_id="test",
                display_name="Test",
                input_price_per_1k=-0.01,
                output_price_per_1k=0.01,
                effective_from=datetime.now(UTC),
            )


class TestCostServicePricingCRUD:
    """Tests for CostService pricing CRUD operations."""

    async def test_create_pricing(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        result = await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=datetime.now(UTC),
            ),
        )
        assert result.model_id == "gpt-4o"
        assert result.input_price_per_1k == 0.0025

    async def test_create_pricing_closes_previous(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        now = datetime.now(UTC)
        await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o Old",
                input_price_per_1k=0.005,
                output_price_per_1k=0.02,
                effective_from=now - timedelta(days=30),
            ),
        )
        await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o New",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=now,
            ),
        )
        result = await service.list_pricing(model_id="gpt-4o")
        entries = result["items"]
        old_entry = [e for e in entries if e.display_name == "GPT-4o Old"][0]
        assert old_entry.effective_until is not None

    async def test_list_pricing(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        for mid in ["gpt-4o", "claude-3.5-sonnet"]:
            await service.create_pricing(
                ModelPricingCreateSchema(
                    model_id=mid,
                    display_name=mid,
                    input_price_per_1k=0.001,
                    output_price_per_1k=0.002,
                    effective_from=datetime.now(UTC),
                ),
            )
        result = await service.list_pricing()
        assert result["total"] == 2

    async def test_list_pricing_filter_by_model(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        for mid in ["gpt-4o", "claude-3.5-sonnet"]:
            await service.create_pricing(
                ModelPricingCreateSchema(
                    model_id=mid,
                    display_name=mid,
                    input_price_per_1k=0.001,
                    output_price_per_1k=0.002,
                    effective_from=datetime.now(UTC),
                ),
            )
        result = await service.list_pricing(model_id="gpt-4o")
        assert result["total"] == 1
        assert result["items"][0].model_id == "gpt-4o"

    async def test_delete_pricing(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        created = await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=datetime.now(UTC),
            ),
        )
        await service.delete_pricing(created.id)
        result = await service.list_pricing()
        assert result["total"] == 0

    async def test_delete_pricing_not_found(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        with pytest.raises(ValueError):
            await service.delete_pricing(uuid.uuid4())

    async def test_get_effective_pricing(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        now = datetime.now(UTC)
        await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=now - timedelta(days=10),
            ),
        )
        result = await service.get_effective_pricing("gpt-4o", now)
        assert result is not None
        assert result.input_price_per_1k == 0.0025

    async def test_get_effective_pricing_not_found(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        result = await service.get_effective_pricing("unknown", datetime.now(UTC))
        assert result is None


class TestCostServiceAggregation:
    """Tests for cost calculation and aggregation."""

    async def test_cost_summary_empty(self, db_session: AsyncSession) -> None:
        service = CostService(db_session)
        result = await service.get_cost_summary(
            start_date=datetime.now(UTC) - timedelta(days=1),
            end_date=datetime.now(UTC),
        )
        assert result.total_cost == 0.0
        assert result.total_input_tokens == 0

    async def test_cost_summary_with_data(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        service = CostService(db_session)
        await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=now - timedelta(days=1),
            ),
        )
        trace = TraceModel(
            trace_id=uuid.uuid4(),
            parent_id=None,
            type="generation",
            name="llm_call",
            start_time=now,
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            metadata_={"model": "gpt-4o"},
            level="DEFAULT",
            status="completed",
        )
        db_session.add(trace)
        await db_session.flush()

        result = await service.get_cost_summary(
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(hours=1),
        )
        assert result.total_input_tokens == 1000
        assert result.total_output_tokens == 500
        assert result.total_cost > 0

    async def test_cost_summary_unpriced(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        service = CostService(db_session)
        trace = TraceModel(
            trace_id=uuid.uuid4(),
            parent_id=None,
            type="generation",
            name="llm_call",
            start_time=now,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            metadata_={"model": "unknown-model"},
            level="DEFAULT",
            status="completed",
        )
        db_session.add(trace)
        await db_session.flush()

        result = await service.get_cost_summary(
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(hours=1),
        )
        assert result.unpriced_tokens == 150
        assert result.total_cost == 0.0

    async def test_cost_breakdown_by_model(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        service = CostService(db_session)
        await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=now - timedelta(days=1),
            ),
        )
        trace = TraceModel(
            trace_id=uuid.uuid4(),
            parent_id=None,
            type="generation",
            name="llm_call",
            start_time=now,
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            metadata_={"model": "gpt-4o"},
            level="DEFAULT",
            status="completed",
        )
        db_session.add(trace)
        await db_session.flush()

        entries = await service.get_cost_breakdown(
            group_by="model",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(hours=1),
        )
        assert len(entries) == 1
        assert entries[0].key == "gpt-4o"
        assert entries[0].percentage == 100.0

    async def test_cost_timeseries_daily(self, db_session: AsyncSession) -> None:
        now = datetime.now(UTC)
        service = CostService(db_session)
        await service.create_pricing(
            ModelPricingCreateSchema(
                model_id="gpt-4o",
                display_name="GPT-4o",
                input_price_per_1k=0.0025,
                output_price_per_1k=0.01,
                effective_from=now - timedelta(days=1),
            ),
        )
        trace = TraceModel(
            trace_id=uuid.uuid4(),
            parent_id=None,
            type="generation",
            name="llm_call",
            start_time=now,
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            metadata_={"model": "gpt-4o"},
            level="DEFAULT",
            status="completed",
        )
        db_session.add(trace)
        await db_session.flush()

        points = await service.get_cost_timeseries(
            granularity="daily",
            start_date=now - timedelta(days=1),
            end_date=now + timedelta(hours=1),
        )
        assert len(points) >= 1
        assert all(p.input_tokens >= 0 for p in points)
