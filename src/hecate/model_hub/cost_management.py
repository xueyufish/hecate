"""Model cost budget management service.

Provides budget CRUD, hierarchical resolution (workspace → agent → user),
z-score anomaly detection, spend forecasting, and chargeback reports.
Integrates with CostService for cost data and PreLLMHook for enforcement.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.model_cost_budget import (
    AnomalySchema,
    BudgetStatusSchema,
    ChargebackEntrySchema,
    ModelCostBudgetCreateSchema,
    ModelCostBudgetModel,
    ModelCostBudgetReadSchema,
    ModelCostBudgetUpdateSchema,
    SpendForecastSchema,
)
from hecate.models.model_pricing import ModelPricingModel
from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")

_DEFAULT_ANOMALY_THRESHOLD = 2.5
_DEFAULT_ROLLING_WINDOW_DAYS = 30
_MIN_HISTORY_DAYS = 7


class CostBudgetService:
    """Service for model cost budget management, anomaly detection, and forecasting."""

    def __init__(
        self,
        db: AsyncSession,
        anomaly_threshold: float = _DEFAULT_ANOMALY_THRESHOLD,
        rolling_window_days: int = _DEFAULT_ROLLING_WINDOW_DAYS,
    ) -> None:
        self._db = db
        self._anomaly_threshold = anomaly_threshold
        self._rolling_window_days = rolling_window_days

    # --- Budget CRUD ---

    async def create_budget(
        self,
        data: ModelCostBudgetCreateSchema,
        workspace_id: uuid.UUID | None = None,
    ) -> ModelCostBudgetReadSchema:
        ws_id = workspace_id or data.workspace_id or _DEFAULT_WORKSPACE
        entry = ModelCostBudgetModel(
            scope=data.scope,
            target_id=data.target_id,
            limit_amount=data.limit_amount,
            period=data.period,
            currency=data.currency,
            policy=data.policy,
            workspace_id=ws_id,
        )
        self._db.add(entry)
        await self._db.flush()
        return ModelCostBudgetReadSchema.model_validate(entry)

    async def list_budgets(
        self,
        workspace_id: uuid.UUID | None = None,
    ) -> list[ModelCostBudgetReadSchema]:
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        stmt = (
            select(ModelCostBudgetModel)
            .where(
                ModelCostBudgetModel.workspace_id == ws_id,
                ~ModelCostBudgetModel.deleted,
            )
            .order_by(ModelCostBudgetModel.scope, ModelCostBudgetModel.created_at)
        )
        result = await self._db.execute(stmt)
        return [ModelCostBudgetReadSchema.model_validate(b) for b in result.scalars().all()]

    async def update_budget(
        self,
        budget_id: uuid.UUID,
        data: ModelCostBudgetUpdateSchema,
    ) -> ModelCostBudgetReadSchema | None:
        stmt = select(ModelCostBudgetModel).where(
            ModelCostBudgetModel.id == budget_id,
            ~ModelCostBudgetModel.deleted,
        )
        result = await self._db.execute(stmt)
        entry = result.scalar_one_or_none()
        if entry is None:
            return None
        if data.limit_amount is not None:
            entry.limit_amount = data.limit_amount
        if data.period is not None:
            entry.period = data.period
        if data.policy is not None:
            entry.policy = data.policy
        await self._db.flush()
        await self._db.refresh(entry)
        return ModelCostBudgetReadSchema.model_validate(entry)

    async def delete_budget(self, budget_id: uuid.UUID) -> bool:
        stmt = select(ModelCostBudgetModel).where(
            ModelCostBudgetModel.id == budget_id,
            ~ModelCostBudgetModel.deleted,
        )
        result = await self._db.execute(stmt)
        entry = result.scalar_one_or_none()
        if entry is None:
            return False
        entry.deleted = True
        entry.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    # --- Budget Status & Enforcement ---

    async def get_budget_status(
        self,
        budget_id: uuid.UUID,
    ) -> BudgetStatusSchema | None:
        stmt = select(ModelCostBudgetModel).where(
            ModelCostBudgetModel.id == budget_id,
            ~ModelCostBudgetModel.deleted,
        )
        result = await self._db.execute(stmt)
        budget = result.scalar_one_or_none()
        if budget is None:
            return None

        spent = await self._calculate_period_spend(budget.workspace_id, budget.period)
        remaining = max(0.0, budget.limit_amount - spent)
        utilization = (spent / budget.limit_amount * 100) if budget.limit_amount > 0 else 0.0

        if utilization >= 100:
            status_band = "breached"
        elif utilization >= 95:
            status_band = "critical"
        elif utilization >= 80:
            status_band = "warning"
        elif utilization >= 50:
            status_band = "caution"
        else:
            status_band = "healthy"

        return BudgetStatusSchema(
            budget_id=budget.id,
            scope=budget.scope,
            target_id=budget.target_id,
            limit_amount=budget.limit_amount,
            spent_amount=round(spent, 6),
            remaining_amount=round(remaining, 6),
            utilization_pct=round(utilization, 2),
            period=budget.period,
            policy=budget.policy,
            status_band=status_band,
        )

    async def check_enforcement(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
    ) -> tuple[bool, str]:
        """Check if an LLM invocation should be blocked by budget policy.

        Returns:
            Tuple of (allowed: bool, reason: str).
            If allowed is False, the invocation should be blocked.
        """
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        budgets = await self.list_budgets(ws_id)

        for budget in budgets:
            if budget.scope == "agent" and budget.target_id != agent_id:
                continue
            if budget.scope == "user":
                continue

            status = await self.get_budget_status(budget.id)
            if status is None:
                continue

            if status.utilization_pct >= 100 and budget.policy == "block":
                return False, (
                    f"Budget exceeded: {status.spent_amount:.2f}/{budget.limit_amount:.2f} "
                    f"({status.utilization_pct:.1f}%) — policy is 'block'"
                )

        return True, ""

    async def _calculate_period_spend(
        self,
        workspace_id: uuid.UUID,
        period: str,
    ) -> float:
        now = datetime.now(UTC)
        if period == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "weekly":
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        stmt = select(TraceModel).where(
            TraceModel.start_time >= start,
            TraceModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        traces = result.scalars().all()

        total_cost = 0.0
        for trace in traces:
            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            model = (trace.metadata_ or {}).get("model", "")
            if model:
                pricing = await self._get_effective_pricing(model, workspace_id)
                if pricing:
                    total_cost += input_tokens / 1000 * pricing["input"] + output_tokens / 1000 * pricing["output"]
        return total_cost

    async def _get_effective_pricing(self, model_id: str, workspace_id: uuid.UUID) -> dict[str, float] | None:
        now = datetime.now(UTC)
        stmt = (
            select(ModelPricingModel)
            .where(
                ModelPricingModel.model_id == model_id,
                ModelPricingModel.effective_from <= now,
                ModelPricingModel.deleted.is_(False),
            )
            .order_by(ModelPricingModel.effective_from.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        pricing = result.scalar_one_or_none()
        if pricing and (pricing.effective_until is None or pricing.effective_until > now):
            return {
                "input": pricing.input_price_per_1k,
                "output": pricing.output_price_per_1k,
            }
        return None

    # --- Anomaly Detection ---

    async def detect_anomalies(
        self,
        workspace_id: uuid.UUID | None = None,
        model_id: str | None = None,
    ) -> list[AnomalySchema]:
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        daily_costs = await self._get_daily_costs(ws_id, model_id)

        if len(daily_costs) < _MIN_HISTORY_DAYS:
            return []

        anomalies = []
        window = daily_costs[-self._rolling_window_days :]
        costs = [c["cost"] for c in window]
        mean = sum(costs) / len(costs)
        variance = sum((c - mean) ** 2 for c in costs) / len(costs)
        stddev = math.sqrt(variance) if variance > 0 else 0.0

        if stddev == 0:
            return []

        for entry in window:
            z_score = (entry["cost"] - mean) / stddev
            if abs(z_score) >= self._anomaly_threshold:
                severity = "info"
                if abs(z_score) >= 4.0:
                    severity = "critical"
                elif abs(z_score) >= 3.0:
                    severity = "warn"

                anomalies.append(
                    AnomalySchema(
                        date=entry["date"],
                        model=entry.get("model", "unknown"),
                        actual_spend=round(entry["cost"], 6),
                        expected_spend=round(mean, 6),
                        z_score=round(z_score, 3),
                        severity=severity,
                    )
                )

        return anomalies

    async def _get_daily_costs(
        self,
        workspace_id: uuid.UUID,
        model_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(days=self._rolling_window_days)
        stmt = select(TraceModel).where(
            TraceModel.start_time >= cutoff,
            TraceModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        traces = result.scalars().all()

        daily: dict[str, dict[str, Any]] = {}
        for trace in traces:
            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            model = (trace.metadata_ or {}).get("model", "unknown")
            if model_id and model != model_id:
                continue

            date_str = trace.start_time.strftime("%Y-%m-%d") if trace.start_time else "unknown"
            pricing = await self._get_effective_pricing(model, workspace_id)
            cost = 0.0
            if pricing:
                cost = input_tokens / 1000 * pricing["input"] + output_tokens / 1000 * pricing["output"]

            key = f"{date_str}:{model}"
            if key not in daily:
                daily[key] = {"date": date_str, "model": model, "cost": 0.0}
            daily[key]["cost"] += cost

        return sorted(daily.values(), key=lambda x: x["date"])

    # --- Spend Forecasting ---

    async def forecast_spend(
        self,
        workspace_id: uuid.UUID | None = None,
        budget_limit: float | None = None,
    ) -> SpendForecastSchema:
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        daily_costs = await self._get_daily_costs(ws_id)

        if len(daily_costs) < 3:
            return SpendForecastSchema(
                projected_amount=0.0,
                confidence_low=0.0,
                confidence_high=0.0,
                status="insufficient_data",
                overrun=0.0,
            )

        costs = [c["cost"] for c in daily_costs]
        n = len(costs)
        x_mean = (n - 1) / 2
        y_mean = sum(costs) / n

        numerator = sum((i - x_mean) * (c - y_mean) for i, c in enumerate(costs))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator != 0 else 0.0
        intercept = y_mean - slope * x_mean

        now = datetime.now(UTC)
        days_in_month = 30
        day_of_month = now.day
        remaining_days = days_in_month - day_of_month

        future_x = n + remaining_days - 1
        projected_daily = slope * future_x + intercept
        current_total = sum(costs)
        projected_total = current_total + projected_daily * remaining_days

        residuals = [c - (slope * i + intercept) for i, c in enumerate(costs)]
        residual_std = math.sqrt(sum(r**2 for r in residuals) / n) if n > 0 else 0.0
        confidence_margin = 1.96 * residual_std * math.sqrt(remaining_days)

        status = "healthy"
        overrun = 0.0
        if budget_limit and projected_total > budget_limit:
            overrun = projected_total - budget_limit
            status = "critical" if overrun > budget_limit * 0.2 else "warning"

        return SpendForecastSchema(
            projected_amount=round(projected_total, 2),
            confidence_low=round(max(0, projected_total - confidence_margin), 2),
            confidence_high=round(projected_total + confidence_margin, 2),
            status=status,
            overrun=round(overrun, 2),
        )

    # --- Chargeback Reports ---

    async def generate_chargeback(
        self,
        workspace_id: uuid.UUID | None = None,
        dimension: str = "agent",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[ChargebackEntrySchema]:
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        now = datetime.now(UTC)
        end = end_date or now
        start = start_date or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        prev_start = (start - timedelta(days=30)).replace(day=1)
        prev_end = start

        current_costs = await self._aggregate_by_dimension(ws_id, dimension, start, end)
        prev_costs = await self._aggregate_by_dimension(ws_id, dimension, prev_start, prev_end)

        entries = []
        for key, data in current_costs.items():
            prev_cost = prev_costs.get(key, {}).get("total", 0.0)
            comparison = ((data["total"] - prev_cost) / prev_cost * 100) if prev_cost > 0 else 0.0

            entries.append(
                ChargebackEntrySchema(
                    dimension=dimension,
                    value=key,
                    total_cost=round(data["total"], 6),
                    top_model=data.get("top_model", ""),
                    period_comparison_pct=round(comparison, 1),
                )
            )

        return sorted(entries, key=lambda x: x.total_cost, reverse=True)

    async def _aggregate_by_dimension(
        self,
        workspace_id: uuid.UUID,
        dimension: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, dict[str, Any]]:
        stmt = select(TraceModel).where(
            TraceModel.start_time >= start,
            TraceModel.start_time < end,
            TraceModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        traces = result.scalars().all()

        agg: dict[str, dict[str, Any]] = {}
        for trace in traces:
            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            model = (trace.metadata_ or {}).get("model", "unknown")
            pricing = await self._get_effective_pricing(model, workspace_id)
            cost = 0.0
            if pricing:
                cost = input_tokens / 1000 * pricing["input"] + output_tokens / 1000 * pricing["output"]

            key = (trace.metadata_ or {}).get(dimension, "unknown") if dimension != "model" else model
            if key not in agg:
                agg[key] = {"total": 0.0, "models": {}}
            agg[key]["total"] += cost
            agg[key]["models"][model] = agg[key]["models"].get(model, 0.0) + cost

        for key in agg:
            models = agg[key]["models"]
            agg[key]["top_model"] = max(models, key=models.get) if models else ""

        return agg


class BudgetExceededError(Exception):
    """Raised when LLM invocation is blocked by budget enforcement."""


class BudgetEnforcementHook:
    """PreLLMHook that enforces cost budget limits before LLM invocations.

    When a workspace budget has policy='block' and utilization >= 100%,
    the hook returns BLOCK with a reason string.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._service = CostBudgetService(db)

    async def check(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
    ) -> tuple[bool, str]:
        """Check if the invocation should be allowed.

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        return await self._service.check_enforcement(workspace_id, agent_id)
