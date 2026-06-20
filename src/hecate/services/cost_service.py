"""Cost tracking service for model pricing management and cost aggregation.

Provides CRUD operations for model pricing entries with time-ranged validity,
and cost calculation from TraceModel token usage data.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.model_pricing import (
    CostBreakdownEntrySchema,
    CostSummarySchema,
    CostTimeseriesPointSchema,
    ModelPricingCreateSchema,
    ModelPricingModel,
    ModelPricingReadSchema,
    ModelPricingUpdateSchema,
)
from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


class CostService:
    """Service for model pricing management and cost aggregation.

    Provides pricing CRUD with time-ranged validity management and
    cost calculation from TraceModel.usage JSON field joined with
    ModelPricingModel rates.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # --- Pricing CRUD ---

    async def create_pricing(
        self,
        data: ModelPricingCreateSchema,
        workspace_id: uuid.UUID | None = None,
    ) -> ModelPricingReadSchema:
        """Create a new pricing entry, closing the previous active entry.

        When a new pricing entry is created for the same model_id, the
        previous active entry's effective_until is set to the new entry's
        effective_from to prevent overlap.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        previous = await self._db.execute(
            select(ModelPricingModel).where(
                ModelPricingModel.model_id == data.model_id,
                ModelPricingModel.workspace_id == ws_id,
                ModelPricingModel.effective_until.is_(None),
                ~ModelPricingModel.deleted,
            ),
        )
        prev_entry = prev_scalar = previous.scalar_one_or_none()
        if prev_entry is not None:
            prev_entry.effective_until = data.effective_from

        entry = ModelPricingModel(
            model_id=data.model_id,
            display_name=data.display_name,
            input_price_per_1k=data.input_price_per_1k,
            output_price_per_1k=data.output_price_per_1k,
            currency=data.currency,
            effective_from=data.effective_from,
            effective_until=None,
            workspace_id=ws_id,
        )
        self._db.add(entry)
        await self._db.flush()
        _ = prev_scalar  # keep reference for clarity
        return ModelPricingReadSchema.model_validate(entry)

    async def list_pricing(
        self,
        workspace_id: uuid.UUID | None = None,
        model_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List pricing entries with optional model_id filter and pagination."""
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        conditions = [
            ModelPricingModel.workspace_id == ws_id,
            ~ModelPricingModel.deleted,
        ]
        if model_id is not None:
            conditions.append(ModelPricingModel.model_id == model_id)

        count_stmt = select(func.count()).select_from(ModelPricingModel).where(*conditions)
        total = (await self._db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = (
            select(ModelPricingModel)
            .where(*conditions)
            .order_by(ModelPricingModel.model_id, ModelPricingModel.effective_from.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self._db.execute(stmt)
        entries = result.scalars().all()

        return {
            "items": [ModelPricingReadSchema.model_validate(e) for e in entries],
            "total": total,
        }

    async def update_pricing(
        self,
        pricing_id: uuid.UUID,
        data: ModelPricingUpdateSchema,
    ) -> ModelPricingReadSchema:
        """Update a pricing entry."""
        result = await self._db.execute(
            select(ModelPricingModel).where(
                ModelPricingModel.id == pricing_id,
                ~ModelPricingModel.deleted,
            ),
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            msg = f"Pricing entry {pricing_id} not found"
            raise ValueError(msg)

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(entry, key, value)

        await self._db.flush()
        return ModelPricingReadSchema.model_validate(entry)

    async def delete_pricing(self, pricing_id: uuid.UUID) -> None:
        """Soft delete a pricing entry."""
        result = await self._db.execute(
            select(ModelPricingModel).where(
                ModelPricingModel.id == pricing_id,
                ~ModelPricingModel.deleted,
            ),
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            msg = f"Pricing entry {pricing_id} not found"
            raise ValueError(msg)

        entry.deleted = True
        entry.deleted_at = datetime.now(UTC)
        await self._db.flush()

    async def get_effective_pricing(
        self,
        model_id: str,
        at_time: datetime,
        workspace_id: uuid.UUID | None = None,
    ) -> ModelPricingModel | None:
        """Find the pricing entry effective at a given point in time."""
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        result = await self._db.execute(
            select(ModelPricingModel).where(
                ModelPricingModel.model_id == model_id,
                ModelPricingModel.workspace_id == ws_id,
                ModelPricingModel.effective_from <= at_time,
                ~ModelPricingModel.deleted,
            ),
        )
        candidates = result.scalars().all()
        for candidate in candidates:
            if candidate.effective_until is None or candidate.effective_until > at_time:
                return candidate
        return None

    # --- Cost Aggregation ---

    async def get_cost_summary(
        self,
        start_date: datetime,
        end_date: datetime,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        model: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> CostSummarySchema:
        """Aggregate total cost and tokens from traces in the given time range."""
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        traces = await self._query_traces(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=ws_id,
        )

        total_cost = 0.0
        total_input = 0
        total_output = 0
        unpriced = 0

        for trace in traces:
            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            total_input += input_tokens
            total_output += output_tokens

            trace_model = model or (trace.metadata_ or {}).get("model", "")
            if trace_model:
                pricing = await self.get_effective_pricing(
                    trace_model,
                    trace.start_time,
                    ws_id,
                )
                if pricing is not None:
                    cost = (
                        input_tokens / 1000 * pricing.input_price_per_1k
                        + output_tokens / 1000 * pricing.output_price_per_1k
                    )
                    total_cost += cost
                else:
                    unpriced += input_tokens + output_tokens
            else:
                unpriced += input_tokens + output_tokens

        return CostSummarySchema(
            total_cost=round(total_cost, 6),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            unpriced_tokens=unpriced,
        )

    async def get_cost_breakdown(
        self,
        group_by: str,
        start_date: datetime,
        end_date: datetime,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        model: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> list[CostBreakdownEntrySchema]:
        """Aggregate cost by a specified dimension."""
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        traces = await self._query_traces(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=ws_id,
        )

        groups: dict[str, dict[str, int | float]] = {}
        for trace in traces:
            if group_by == "model":
                key = (trace.metadata_ or {}).get("model", "unknown")
            elif group_by == "agent":
                key = str(trace.agent_id) if trace.agent_id else "unknown"
            elif group_by == "user":
                key = str(trace.user_id) if trace.user_id else "unknown"
            elif group_by == "session":
                key = str(trace.session_id) if trace.session_id else "unknown"
            else:
                msg = f"Invalid group_by value: {group_by}"
                raise ValueError(msg)

            if key not in groups:
                groups[key] = {"cost": 0.0, "input_tokens": 0, "output_tokens": 0}

            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            groups[key]["input_tokens"] += input_tokens
            groups[key]["output_tokens"] += output_tokens

            trace_model = (trace.metadata_ or {}).get("model", "")
            if trace_model:
                pricing = await self.get_effective_pricing(
                    trace_model,
                    trace.start_time,
                    ws_id,
                )
                if pricing is not None:
                    groups[key]["cost"] += (
                        input_tokens / 1000 * pricing.input_price_per_1k
                        + output_tokens / 1000 * pricing.output_price_per_1k
                    )

        total_cost = sum(g["cost"] for g in groups.values()) or 1.0
        entries = []
        for key, vals in groups.items():
            entries.append(
                CostBreakdownEntrySchema(
                    key=key,
                    cost=round(vals["cost"], 6),
                    input_tokens=vals["input_tokens"],
                    output_tokens=vals["output_tokens"],
                    percentage=round(vals["cost"] / total_cost * 100, 2),
                ),
            )

        entries.sort(key=lambda e: e.cost, reverse=True)
        return entries

    async def get_cost_timeseries(
        self,
        granularity: str,
        start_date: datetime,
        end_date: datetime,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        model: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> list[CostTimeseriesPointSchema]:
        """Aggregate cost into time buckets."""
        ws_id = workspace_id or _DEFAULT_WORKSPACE

        traces = await self._query_traces(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=ws_id,
        )

        buckets: dict[datetime, dict[str, int | float]] = {}
        for trace in traces:
            bucket_key = _truncate_time(trace.start_time, granularity)
            if bucket_key not in buckets:
                buckets[bucket_key] = {"cost": 0.0, "input_tokens": 0, "output_tokens": 0}

            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            buckets[bucket_key]["input_tokens"] += input_tokens
            buckets[bucket_key]["output_tokens"] += output_tokens

            trace_model = model or (trace.metadata_ or {}).get("model", "")
            if trace_model:
                pricing = await self.get_effective_pricing(
                    trace_model,
                    trace.start_time,
                    ws_id,
                )
                if pricing is not None:
                    buckets[bucket_key]["cost"] += (
                        input_tokens / 1000 * pricing.input_price_per_1k
                        + output_tokens / 1000 * pricing.output_price_per_1k
                    )

        return [
            CostTimeseriesPointSchema(
                timestamp=ts,
                cost=round(vals["cost"], 6),
                input_tokens=vals["input_tokens"],
                output_tokens=vals["output_tokens"],
            )
            for ts, vals in sorted(buckets.items())
        ]

    async def _query_traces(
        self,
        start_date: datetime,
        end_date: datetime,
        user_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> list[TraceModel]:
        """Query traces with optional filters, returning GENERATION type only."""
        conditions = [
            TraceModel.type == "generation",
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            TraceModel.usage.isnot(None),
            ~TraceModel.deleted,
        ]
        if user_id is not None:
            conditions.append(TraceModel.user_id == user_id)
        if agent_id is not None:
            conditions.append(TraceModel.agent_id == agent_id)
        if session_id is not None:
            conditions.append(TraceModel.session_id == session_id)

        _ = workspace_id  # workspace filtering at trace level is via session/agent
        stmt = select(TraceModel).where(*conditions)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())


def _truncate_time(dt: datetime, granularity: str) -> datetime:
    """Truncate datetime to the specified granularity bucket."""
    if granularity == "hourly":
        return dt.replace(minute=0, second=0, microsecond=0)
    if granularity == "daily":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if granularity == "monthly":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    msg = f"Invalid granularity: {granularity}"
    raise ValueError(msg)
