"""Evaluation engine — orchestrates batch evaluation runs.

The :class:`EvaluationEngine` accepts a list of evaluators and a dataset,
runs every evaluator against every dataset item, collects scores, computes
per-metric averages, and persists results to the database.

Startup registration of built-in evaluators with :class:`PluginRegistry`
is handled by :func:`register_evaluators`. The same function also writes
to the module-private :data:`_EVALUATOR_CLASS_REGISTRY` index so API
consumers can resolve ``name → class`` without touching the registry.
Use :func:`get_evaluator_class` for the public lookup.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.plugin.manifest import PluginManifest
from hecate.core.plugin.registry import PluginRegistry
from hecate.models.evaluation import (
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    RunStatus,
)
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.types import (
    AnswerSource,
    EvalInput,
    EvaluationRunResult,
    Score,
    Timer,
)

logger = logging.getLogger(__name__)


# Module-private class index populated by ``register_evaluators``. The
# ``PluginRegistry`` holds evaluator instances + manifests; this dict
# holds the raw classes for callers that need to instantiate on demand
# (e.g. ``api/evaluation.py:create_run``). The two are written together
# in :func:`register_evaluators` and never diverge.
_EVALUATOR_CLASS_REGISTRY: dict[str, type[Evaluator]] = {}


def get_evaluator_class(name: str) -> type[Evaluator] | None:
    """Resolve an evaluator canonical short name to its class.

    Reads the module-private class index written by
    :func:`register_evaluators` at application startup. Returns ``None``
    when no evaluator with that name is registered.
    """
    return _EVALUATOR_CLASS_REGISTRY.get(name)


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
        tags: list[str] | None = None,
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
            tags: Optional tag filter (OR semantics). When provided, only
                items whose ``tags`` JSON array contains any of the
                specified values are scored.

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
            items = list(result.scalars().all())

            # Apply tag filter (Python-side; see comment in dataset_service)
            if tags:
                wanted = set(tags)
                items = [it for it in items if any(t in wanted for t in (it.tags or []))]

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
            from hecate_llm.service import LLMService

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
    """Register all 16 built-in evaluators with the PluginRegistry.

    Imports and registers every evaluator subclass under ``type="evaluator"``.
    Each entry is wrapped in its own ``try/except`` so that an optional
    dependency failure (e.g. ``ragas`` missing) skips only the relevant
    evaluator(s) — the remaining 12 still register successfully.

    The function also writes the class itself into the module-private
    :data:`_EVALUATOR_CLASS_REGISTRY` so API consumers can resolve
    ``name → class`` via :func:`get_evaluator_class`.

    Should be called once at application startup (see
    ``core/composition/wiring.py``).

    Args:
        registry: The PluginRegistry instance to register evaluators with.

    Returns:
        Number of evaluators successfully registered.
    """
    # Canonical 16-evaluator manifest. Order matches the four-scope
    # taxonomy declared in ``builtin-evaluators/spec.md``. ``scope`` is
    # informational metadata for tooling — the API exposes it via
    # ``GET /api/evaluation/evaluators``.
    _manifests: list[tuple[str, str, str, str]] = [
        # (module:ClassName, scope, source, kind)
        # Result Layer (5) — LLM-as-Judge
        ("hecate.ops.evaluation.agent_evaluators:CorrectnessEvaluator", "result", "llm_judge", "agent"),
        ("hecate.ops.evaluation.agent_evaluators:RelevancyEvaluator", "result", "llm_judge", "agent"),
        ("hecate.ops.evaluation.agent_evaluators:CompletenessEvaluator", "result", "llm_judge", "agent"),
        # Result Layer (4) — deterministic
        ("hecate.ops.evaluation.format_evaluators:ContainsEvaluator", "result", "deterministic", "format"),
        ("hecate.ops.evaluation.format_evaluators:ExactMatchEvaluator", "result", "deterministic", "format"),
        ("hecate.ops.evaluation.format_evaluators:IsJsonEvaluator", "result", "deterministic", "format"),
        ("hecate.ops.evaluation.format_evaluators:RegexMatchEvaluator", "result", "deterministic", "format"),
        # Process Layer (2) — LLM-as-Judge
        ("hecate.ops.evaluation.agent_evaluators:ToolCallAccuracyEvaluator", "process", "llm_judge", "agent"),
        ("hecate.ops.evaluation.agent_evaluators:TaskCompletionEvaluator", "process", "llm_judge", "agent"),
        # RAG Layer (4) — optional (ragas dependency)
        ("hecate.ops.evaluation.rag_evaluators:ContextPrecisionEvaluator", "rag", "ragas", "rag"),
        ("hecate.ops.evaluation.rag_evaluators:ContextRecallEvaluator", "rag", "ragas", "rag"),
        ("hecate.ops.evaluation.rag_evaluators:FaithfulnessEvaluator", "rag", "ragas", "rag"),
        ("hecate.ops.evaluation.rag_evaluators:AnswerRelevancyEvaluator", "rag", "ragas", "rag"),
        # Safety Layer (5) — 4 LLM-as-Judge + 1 deterministic (pii_leakage)
        ("hecate.ops.evaluation.safety_evaluators:RefusalEvaluator", "safety", "llm_judge", "safety"),
        ("hecate.ops.evaluation.safety_evaluators:HarmfulnessEvaluator", "safety", "llm_judge", "safety"),
        ("hecate.ops.evaluation.safety_evaluators:PiiLeakageEvaluator", "safety", "deterministic", "safety"),
    ]

    import importlib

    count = 0
    for entry, _scope, _source, kind in _manifests:
        try:
            module_path, _, class_name = entry.partition(":")
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            instance = cls()
            manifest = PluginManifest(
                type="evaluator",
                name=instance.name,
                version="1.0.0",
                api_version="1.0",
                min_platform_version="0.5.0",
                description=instance.description,
                entry=f"python:{module_path}:{class_name}",
            )
            registry.register(manifest, instance)
            _EVALUATOR_CLASS_REGISTRY[instance.name] = cls
            count += 1
        except ImportError as exc:
            # ragas missing — surface as warning, not fatal
            if kind == "rag":
                logger.warning("Skipping rag evaluator (missing dependency): %s", entry)
            else:
                logger.exception("Failed to import evaluator %s: %s", entry, exc)
        except Exception:
            logger.exception("Failed to register evaluator %s", entry)

    logger.info(
        "Registered %d/%d built-in evaluators (4 rag evaluators skipped if ragas missing)",
        count,
        len(_manifests),
    )
    return count
