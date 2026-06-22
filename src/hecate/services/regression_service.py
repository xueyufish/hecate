"""Regression testing service for evaluation quality monitoring.

Provides run comparison, pass/fail computation, and regression detection
for CI/CD integration and quality gating.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationScoreModel,
)
from hecate.services.evaluation.engine import EvaluationEngine
from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.registry import get_evaluator
from hecate.services.evaluation.types import AnswerSource, EvaluationRunResult

logger = logging.getLogger(__name__)


class RegressionService:
    """Manage evaluation regression detection and pass/fail computation.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compare_runs(
        self,
        baseline_run_id: uuid.UUID,
        candidate_run_id: uuid.UUID,
        threshold: float = 0.05,
    ) -> dict:
        """Compare two evaluation runs and detect regressions.

        Args:
            baseline_run_id: UUID of the baseline run.
            candidate_run_id: UUID of the candidate run.
            threshold: Minimum score drop to flag as regression (default 5%).

        Returns:
            Dict with overall_regressed flag, per-metric deltas, and regressions list.
        """
        baseline_metrics = await self._compute_run_averages(baseline_run_id)
        candidate_metrics = await self._compute_run_averages(candidate_run_id)

        all_metrics = sorted(set(baseline_metrics) | set(candidate_metrics))
        regressions: list[dict] = []
        metric_deltas: dict[str, float] = {}

        for metric in all_metrics:
            base_val = baseline_metrics.get(metric, 0.0)
            cand_val = candidate_metrics.get(metric, 0.0)
            delta = cand_val - base_val
            metric_deltas[metric] = delta
            if delta < -threshold:
                regressions.append(
                    {
                        "metric": metric,
                        "baseline_avg": base_val,
                        "candidate_avg": cand_val,
                        "delta": delta,
                        "is_regression": True,
                    }
                )

        return {
            "baseline_run_id": str(baseline_run_id),
            "candidate_run_id": str(candidate_run_id),
            "overall_regressed": len(regressions) > 0,
            "regressions": regressions,
            "metric_deltas": metric_deltas,
            "baseline_metric_averages": baseline_metrics,
            "candidate_metric_averages": candidate_metrics,
        }

    async def compute_item_pass_fail(
        self,
        item: EvaluationItemModel,
        scores: list[EvaluationScoreModel],
        dataset_default_threshold: float | None = None,
    ) -> dict:
        """Compute pass/fail for a single item based on assertions or thresholds.

        Args:
            item: The evaluation item with optional assertions.
            scores: List of score models for this item.
            dataset_default_threshold: Fallback threshold if item has no assertions.

        Returns:
            Dict with passed flag, total, passed_count, and failed details.
        """
        assertions = item.assertions if item.assertions else []
        failed: list[dict] = []

        if assertions:
            for assertion in assertions:
                atype = assertion.get("type", "")
                athreshold = assertion.get("threshold")

                if atype in ("contains", "contains_any", "regex_match", "is_json", "exact_match"):
                    continue

                if athreshold is not None:
                    matching = [s for s in scores if s.metric_name == atype]
                    for s in matching:
                        if s.value < 0:
                            continue
                        if s.value < athreshold:
                            failed.append(
                                {
                                    "type": atype,
                                    "threshold": athreshold,
                                    "actual": s.value,
                                }
                            )
        elif dataset_default_threshold is not None:
            for s in scores:
                if s.value < 0:
                    continue
                if s.value < dataset_default_threshold:
                    failed.append(
                        {
                            "type": s.metric_name,
                            "threshold": dataset_default_threshold,
                            "actual": s.value,
                        }
                    )

        if assertions:
            total = len(assertions)
        elif dataset_default_threshold is not None:
            total = len([s for s in scores if s.value >= 0])
        else:
            total = 0
        return {
            "passed": len(failed) == 0,
            "total": total if total > 0 else len(scores),
            "passed_count": (total if total > 0 else len(scores)) - len(failed),
            "failed": failed,
        }

    async def run_regression(
        self,
        dataset_id: uuid.UUID,
        evaluator_names: list[str],
        tags: list[str] | None = None,
        threshold: float = 0.05,
        baseline_run_id: uuid.UUID | None = None,
    ) -> dict:
        """Execute an evaluation run and compare against baseline.

        Args:
            dataset_id: Dataset to evaluate.
            evaluator_names: List of evaluator names to run.
            tags: Optional tag filter for items.
            threshold: Regression threshold.
            baseline_run_id: Optional baseline run for comparison.

        Returns:
            Structured regression report dict.
        """
        evaluators: list[Evaluator] = []
        for name in evaluator_names:
            cls = get_evaluator(name)
            if cls is None:
                msg = f"Unknown evaluator: {name!r}"
                raise ValueError(msg)
            evaluators.append(cls())

        engine = EvaluationEngine(self.db)
        result: EvaluationRunResult = await engine.run(
            evaluators,
            dataset_id,
            answer_source=AnswerSource.MANUAL,
            tags=tags,
        )

        dataset = await self.db.get(EvaluationDatasetModel, dataset_id)
        default_threshold = dataset.default_threshold if dataset else None

        items_stmt = select(EvaluationItemModel).where(
            EvaluationItemModel.dataset_id == dataset_id,
            ~EvaluationItemModel.deleted,
        )
        if tags:
            items_stmt = items_stmt.where(EvaluationItemModel.tags.op("&&")(tags))
        items_result = await self.db.execute(items_stmt)
        items = items_result.scalars().all()

        scores_stmt = select(EvaluationScoreModel).where(
            EvaluationScoreModel.run_id == result.run_id,
        )
        scores_result = await self.db.execute(scores_stmt)
        all_scores = scores_result.scalars().all()

        passed_items = 0
        failed_items = 0
        for item in items:
            item_scores = [s for s in all_scores if s.item_id == item.id]
            pf = await self.compute_item_pass_fail(item, item_scores, default_threshold)
            if pf["passed"]:
                passed_items += 1
            else:
                failed_items += 1

        regressions: list[dict] = []
        if baseline_run_id:
            comparison = await self.compare_runs(baseline_run_id, result.run_id, threshold)
            regressions = comparison["regressions"]
            overall_regressed = comparison["overall_regressed"]
        else:
            overall_regressed = failed_items > 0

        passed = passed_items > 0 and failed_items == 0 and not overall_regressed

        if regressions:
            await self._trigger_regression_alert(result.run_id, regressions, dataset_id)

        return {
            "run_id": str(result.run_id),
            "passed": passed,
            "total_items": result.total_items,
            "passed_items": passed_items,
            "failed_items": failed_items,
            "regressions": regressions,
            "metric_averages": result.metric_averages,
            "total_duration_ms": result.total_duration_ms,
        }

    async def _compute_run_averages(self, run_id: uuid.UUID) -> dict[str, float]:
        """Compute per-metric averages for a run."""
        stmt = select(EvaluationScoreModel).where(EvaluationScoreModel.run_id == run_id)
        result = await self.db.execute(stmt)
        scores = result.scalars().all()

        metric_values: dict[str, list[float]] = {}
        for s in scores:
            if s.value >= 0:
                metric_values.setdefault(s.metric_name, []).append(s.value)

        return {name: sum(vals) / len(vals) for name, vals in metric_values.items() if vals}

    async def _trigger_regression_alert(
        self,
        run_id: uuid.UUID,
        regressions: list[dict],
        dataset_id: uuid.UUID,
    ) -> None:
        """Create an alert event when regression is detected."""
        try:
            from hecate.services.alert_service import AlertService

            avg_delta = sum(r["delta"] for r in regressions) / len(regressions) if regressions else 0.0
            alert_svc = AlertService(self.db)
            await alert_svc.create_event(
                alert_type="evaluation_regression",
                current_value=abs(avg_delta),
                rule_name=f"Evaluation regression on dataset {dataset_id}",
                metadata={
                    "run_id": str(run_id),
                    "dataset_id": str(dataset_id),
                    "regressions": regressions,
                },
                workspace_id=uuid.UUID(int=0),
            )
        except Exception as e:
            logger.warning("Failed to trigger regression alert: %s", e)
