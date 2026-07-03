"""Evaluation engine — orchestrates batch evaluation runs.

The :class:`EvaluationEngine` accepts a list of evaluators and a dataset,
runs every evaluator against every dataset item, collects scores, computes
per-metric averages, and persists results to the database.

Startup registration of built-in evaluators with :class:`PluginRegistry`
is handled by :func:`register_evaluators`.
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
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry
from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.types import (
    AnswerSource,
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
        answer_source: AnswerSource = AnswerSource.MANUAL,
    ) -> EvaluationRunResult:
        """Execute all evaluators against all items in a dataset.

        Creates an ``EvaluationRunModel`` record, iterates over items and
        evaluators in nested loops, catches per-item-per-evaluator errors,
        persists scores, and computes per-metric averages.

        Args:
            evaluators: List of evaluator instances to run.
            dataset_id: UUID of the dataset to evaluate.
            answer_source: How to obtain generated answers — manual (from items),
                pipeline (run RAG), or auto (fallback).

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
                    generated = item.generated_answer or ""

                    if answer_source in (AnswerSource.PIPELINE, AnswerSource.AUTO) and not generated:
                        generated = await self._generate_answer_via_pipeline(item.query, item.context or [])

                    eval_input = EvalInput(
                        query=item.query,
                        retrieved_contexts=item.context or [],
                        generated_answer=generated,
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

    async def _generate_answer_via_pipeline(
        self,
        query: str,
        contexts: list[str],
    ) -> str:
        """Generate an answer using the RAG pipeline.

        Falls back to a simple context-based answer when the LLM service
        is unavailable.

        Args:
            query: The user query.
            contexts: Retrieved context passages.

        Returns:
            Generated answer string.
        """
        if not contexts:
            return ""

        try:
            from hecate.services.llm.service import LLMService

            context_text = "\n\n".join(contexts)
            messages = [
                {
                    "role": "system",
                    "content": "Answer the question based on the provided context. Be concise and accurate.",
                },
                {
                    "role": "user",
                    "content": f"Context:\n{context_text}\n\nQuestion: {query}",
                },
            ]
            llm = LLMService()
            response = await llm.chat(messages=messages, model="gpt-4o-mini")
            return (response.content or "").strip()
        except Exception as e:
            logger.warning("Pipeline answer generation failed: %s", e)
            return ""


def register_evaluators(registry: PluginRegistry) -> int:
    """Register all built-in evaluators with the PluginRegistry.

    Imports and registers all evaluator subclasses under type="evaluator".
    Should be called once at application startup.

    Args:
        registry: The PluginRegistry instance to register evaluators with.

    Returns:
        Number of evaluators registered.
    """
    from hecate.services.evaluation.agent_evaluators import (
        CompletenessEvaluator,
        CorrectnessEvaluator,
        RelevancyEvaluator,
        TaskCompletionEvaluator,
        ToolCallAccuracyEvaluator,
    )
    from hecate.services.evaluation.rag_evaluators import (
        AnswerRelevancyEvaluator,
        ContextPrecisionEvaluator,
        ContextRecallEvaluator,
        FaithfulnessEvaluator,
    )

    evaluator_classes = [
        # RAG evaluators
        ContextPrecisionEvaluator,
        ContextRecallEvaluator,
        FaithfulnessEvaluator,
        AnswerRelevancyEvaluator,
        # Agent evaluators
        CorrectnessEvaluator,
        RelevancyEvaluator,
        CompletenessEvaluator,
        ToolCallAccuracyEvaluator,
        TaskCompletionEvaluator,
    ]

    count = 0
    for cls in evaluator_classes:
        try:
            # mypy infers the list as type[BuiltinEvaluator] (abstract);
            # all 9 entries are concrete subclasses, so this is safe.
            instance = cls()  # type: ignore[abstract]
            manifest = PluginManifest(
                type="evaluator",
                name=instance.name,
                version="1.0.0",
                api_version="1.0",
                min_platform_version="0.5.0",
                description=instance.description,
            )
            registry.register(manifest, instance)
            count += 1
        except Exception:
            logger.exception("Failed to register evaluator %s", cls.__name__)

    logger.info("Registered %d built-in evaluators", count)
    return count
