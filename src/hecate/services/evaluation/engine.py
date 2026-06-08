"""Evaluation engine — orchestrates batch evaluation runs.

The :class:`EvaluationEngine` accepts a list of evaluators and a dataset,
runs every evaluator against every dataset item, collects scores, computes
per-metric averages, and persists results to the database.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    RunStatus,
)
from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.types import (
    EvalInput,
    EvaluationRunResult,
    Score,
    Timer,
)

logger = logging.getLogger(__name__)


class EvaluationEngine:
    """Orchestrate batch evaluation of evaluators against dataset items.

    Args:
        db: Async SQLAlchemy session for database operations.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(
        self,
        evaluators: list[Evaluator],
        dataset_id: uuid.UUID,
    ) -> EvaluationRunResult:
        """Execute all evaluators against all items in a dataset.

        Creates an ``EvaluationRunModel`` record, iterates over items and
        evaluators in nested loops, catches per-item-per-evaluator errors,
        persists scores, and computes per-metric averages.

        Args:
            evaluators: List of evaluator instances to run.
            dataset_id: UUID of the dataset to evaluate.

        Returns:
            Aggregated :class:`EvaluationRunResult` with scores and averages.

        Raises:
            Exception: Re-raises any unhandled error after marking the run as
                failed in the database.
        """
        run = EvaluationRunModel(
            dataset_id=dataset_id,
            status=RunStatus.RUNNING.value,
            evaluator_configs=[e.name for e in evaluators],
        )
        self.db.add(run)
        await self.db.flush()

        run.started_at = datetime.now(UTC)
        await self.db.flush()

        try:
            # Fetch all non-deleted items for the dataset
            stmt = select(EvaluationItemModel).where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
            result = await self.db.execute(stmt)
            items = result.scalars().all()

            item_scores: dict[str, list[Score]] = {}
            all_metric_values: dict[str, list[float]] = {}

            with Timer() as total_timer:
                for item in items:
                    eval_input = EvalInput(
                        query=item.query,
                        retrieved_contexts=item.context or [],
                        generated_answer="",
                        expected_answer=item.expected_answer,
                    )

                    item_score_list: list[Score] = []

                    for evaluator in evaluators:
                        try:
                            output = await evaluator.evaluate(eval_input)
                            for score in output.scores:
                                item_score_list.append(score)
                        except Exception as e:
                            logger.error(
                                "Evaluator %s failed on item %s: %s",
                                evaluator.name,
                                item.id,
                                e,
                            )
                            error_score = Score(
                                metric_name=evaluator.name,
                                value=-1.0,
                                reasoning=f"Evaluator error: {e}",
                                source="llm_judge",
                            )
                            item_score_list.append(error_score)

                    item_scores[str(item.id)] = item_score_list

                    # Persist scores to database
                    for score in item_score_list:
                        score_model = EvaluationScoreModel(
                            run_id=run.id,
                            item_id=item.id,
                            metric_name=score.metric_name,
                            value=score.value,
                            reasoning=score.reasoning,
                            source=score.source,
                        )
                        self.db.add(score_model)

                        # Track for averages (exclude error scores)
                        if score.value >= 0:
                            all_metric_values.setdefault(score.metric_name, []).append(score.value)

            await self.db.flush()

            # Compute per-metric averages
            metric_averages: dict[str, float] = {}
            for metric_name, values in all_metric_values.items():
                if values:
                    metric_averages[metric_name] = sum(values) / len(values)

            # Mark run as completed
            run.status = RunStatus.COMPLETED.value
            run.completed_at = datetime.now(UTC)
            await self.db.flush()

            return EvaluationRunResult(
                run_id=run.id,
                dataset_id=dataset_id,
                item_scores=item_scores,
                metric_averages=metric_averages,
                total_items=len(items),
                total_duration_ms=total_timer.elapsed_ms,
            )

        except Exception:
            run.status = RunStatus.FAILED.value
            run.completed_at = datetime.now(UTC)
            await self.db.flush()
            raise
