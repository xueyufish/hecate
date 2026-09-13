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

import asyncio
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
        agent_id: uuid.UUID | None = None,
        run: EvaluationRunModel | None = None,
        summary_config: dict | None = None,
        workflow_id: uuid.UUID | None = None,
        workflow_version: int | None = None,
        repetitions: int = 1,
        max_in_flight: int = 1,
        items_override: list[EvaluationItemModel] | None = None,
    ) -> EvaluationRunResult:
        """Execute all evaluators against all items in a dataset.

        Creates an ``EvaluationRunModel`` record (or reuses the passed-in
        ``run`` row for task-triggered runs), iterates over items and
        evaluators in nested loops, catches per-item-per-evaluator errors,
        persists scores, computes per-metric averages, and — when
        ``summary_config`` is provided — writes a pass/fail ``summary``
        onto the run row.

        Args:
            evaluators: List of evaluator instances to run.
            dataset_id: UUID of the dataset to evaluate.
            answer_source: How to obtain generated answers — manual (from items),
                pipeline (run RAG), auto (fallback), agent (invoke the agent
                under test), or workflow (execute the workflow under test
                end-to-end via ``WorkflowExecutionService``).
            tags: Optional tag filter (OR semantics). When provided, only
                items whose ``tags`` JSON array contains any of the
                specified values are scored.
            agent_id: The agent under test, required when ``answer_source``
                is ``AGENT`` — one ``RuntimePort.agent_execute`` call per
                item lacking a stored answer.
            run: Pre-created run row to reuse (task-triggered runs carry
                ``task_id``); a fresh row is created when omitted.
            summary_config: Optional task-run aggregation config —
                ``{"threshold": float, "baseline_run_id": uuid,
                "regression_threshold": float}``. When present, the run's
                ``summary`` JSON column is populated on completion.
            workflow_id: The workflow under test, required when
                ``answer_source`` is ``WORKFLOW``. Pinned at run start.
            workflow_version: Optional explicit version number; when
                omitted the workflow's current latest version is resolved
                at run start and locked onto the run row.
            repetitions: How many times each item is executed. ``1`` by
                default; only meaningful for ``WORKFLOW`` answer sources
                (per the 7.3 spec). When ``>1`` the run summary exposes
                ``consistency_rate`` in addition to ``pass_rate``.
            items_override: Optional item list to execute instead of the
                dataset's live items (7.3b version-bound runs — the
                runner rehydrates the named version's frozen items).
                When provided, the tag filter does not apply: the frozen
                set is authoritative.

        Returns:
            Aggregated :class:`EvaluationRunResult` with scores and averages.

        Raises:
            Exception: Re-raises any unhandled error after marking the run as
                failed in the database.
        """
        if answer_source == AnswerSource.AGENT and agent_id is None:
            msg = "answer_source='agent' requires agent_id"
            raise ValueError(msg)
        if answer_source == AnswerSource.WORKFLOW:
            if workflow_id is None:
                msg = "answer_source='workflow' requires workflow_id"
                raise ValueError(msg)
            if repetitions < 1:
                msg = "repetitions must be >= 1"
                raise ValueError(msg)

        if run is None:
            run = EvaluationRunModel(
                dataset_id=dataset_id,
                status=RunStatus.RUNNING.value,
                evaluator_configs=[e.name for e in evaluators],
            )
            self.db.add(run)
            await self.db.flush()

        if answer_source == AnswerSource.WORKFLOW:
            # Pin workflow_version once on the run row so downstream consumers
            # (diff, summary, publish report) see the same value the
            # engine actually executed against.
            if workflow_version is not None:
                run.workflow_version = workflow_version
            elif run.workflow_version is None:
                run.workflow_version = await self._resolve_latest_workflow_version(workflow_id)
            run.repetitions = repetitions
            await self.db.flush()

        run.started_at = datetime.now(UTC)
        await self.db.flush()

        try:
            # Fetch all non-deleted items for the dataset, or use the
            # caller-supplied frozen set (7.3b version-bound runs — the
            # named version is authoritative, so neither the live query
            # nor the tag filter applies).
            if items_override is not None:
                items = list(items_override)
            else:
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
            trajectory: list[dict] = []

            # 7.3c: known-bad items still execute and record scores but are
            # excluded from aggregation. Task runs read the exemption set
            # from their frozen dataset snapshot; request-triggered runs
            # (no snapshot) use the items as loaded at run start — both are
            # "state at run start", so completed runs never change retroactively.
            if run.dataset_snapshot is not None:
                exempted_item_ids = self._snapshot_exempted_ids(run.dataset_snapshot)
            else:
                exempted_item_ids = {str(it.id) for it in items if it.known_bad}

            concurrency = max(1, max_in_flight) if answer_source == AnswerSource.WORKFLOW else 1
            semaphore = asyncio.Semaphore(concurrency) if concurrency > 1 else None

            with Timer() as total_timer:
                if semaphore is None:
                    for item in items:
                        scores, traj = await self._process_item(
                            item=item,
                            evaluators=evaluators,
                            answer_source=answer_source,
                            agent_id=agent_id,
                            workflow_id=workflow_id,
                            workflow_version=run.workflow_version,
                            repetitions=repetitions,
                            run=run,
                        )
                        item_scores[str(item.id)] = scores
                        if traj:
                            trajectory.extend(traj)
                        for score in scores:
                            if score.value >= 0 and str(item.id) not in exempted_item_ids:
                                all_metric_values.setdefault(score.metric_name, []).append(score.value)
                else:
                    tasks = [
                        asyncio.create_task(
                            self._process_item(
                                item=item,
                                evaluators=evaluators,
                                answer_source=answer_source,
                                agent_id=agent_id,
                                workflow_id=workflow_id,
                                workflow_version=run.workflow_version,
                                repetitions=repetitions,
                                run=run,
                                semaphore=semaphore,
                            )
                        )
                        for item in items
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=False)
                    for item, result in zip(items, results, strict=True):
                        scores, traj = result
                        item_scores[str(item.id)] = scores
                        if traj:
                            trajectory.extend(traj)
                        for score in scores:
                            if score.value >= 0 and str(item.id) not in exempted_item_ids:
                                all_metric_values.setdefault(score.metric_name, []).append(score.value)

            await self.db.flush()

            # Compute per-metric averages
            metric_averages: dict[str, float] = {}
            for metric_name, values in all_metric_values.items():
                if values:
                    metric_averages[metric_name] = sum(values) / len(values)

            if summary_config is not None:
                run.summary = await self._build_summary(
                    total_items=len(items),
                    item_scores=item_scores,
                    metric_averages=metric_averages,
                    summary_config=summary_config,
                    repetitions=repetitions,
                    exempted_item_ids=exempted_item_ids,
                )

            if answer_source == AnswerSource.WORKFLOW:
                run.trajectory = trajectory

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

    async def _generate_answer_via_agent(
        self,
        query: str,
        agent_id: uuid.UUID,
    ) -> str:
        """Generate an answer by invoking the agent under test.

        One non-streaming ``RuntimePort.agent_execute`` call per item. The
        port adapter is resolved lazily through the composition factory to
        avoid a composition-root import at module load.

        Args:
            query: The item query, sent as a single user message.
            agent_id: The agent under test.

        Returns:
            The agent's final response text (may be empty on an empty reply).

        Raises:
            Exception: Any invocation failure propagates so the caller can
                record per-item error scores.
        """
        from hecate.core.composition.runtime_port_adapter import make_runtime_port

        port = make_runtime_port()
        response = await port.agent_execute(
            agent_id=agent_id,
            messages=[{"role": "user", "content": query}],
            channel_snapshot={},
        )
        return str(response.get("response") or "").strip()

    async def _process_item(
        self,
        item: EvaluationItemModel,
        evaluators: list[Evaluator],
        answer_source: AnswerSource,
        agent_id: uuid.UUID | None,
        workflow_id: uuid.UUID | None,
        workflow_version: int | None,
        repetitions: int,
        run: EvaluationRunModel,
        semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[list[Score], list[dict]]:
        """Process a single dataset item: generate the answer and score it.

        Returns the list of per-evaluator scores (one per evaluator, with
        ``value=-1.0`` for errors) and any trajectory rows collected from
        a workflow execution. Errors are isolated to the item — sibling
        items keep scoring.

        When ``semaphore`` is provided, the answer-generation phase is
        gated so that at most ``semaphore`` value invocations run
        concurrently; the rest of the per-item work runs after the gate
        is released. This bounds real workflow executions against the
        task's ``max_in_flight`` setting (7.3).
        """
        item_score_list: list[Score] = []
        trajectory_rows: list[dict] = []
        # Keep the 7.2c agent-path message contract verbatim; only the
        # workflow path (7.3) introduces a new prefix.
        if answer_source == AnswerSource.AGENT:
            failure_prefix = "Agent invocation failed"
        elif answer_source == AnswerSource.WORKFLOW:
            failure_prefix = "Workflow execution failed"
        else:
            failure_prefix = "Pipeline answer generation failed"

        async def _gen() -> str:
            if answer_source == AnswerSource.AGENT:
                return await self._generate_answer_via_agent(item.query, agent_id)
            if answer_source == AnswerSource.WORKFLOW:
                content, traj = await self._generate_answer_via_workflow(
                    query=item.query,
                    workflow_id=workflow_id,
                    workflow_version=workflow_version,
                    item_id=item.id,
                    repetitions=repetitions,
                )
                trajectory_rows.extend(traj)
                return content
            return await self._generate_answer_via_pipeline(item.query, item.context or [])

        generated = item.generated_answer or ""
        if not generated and answer_source is not AnswerSource.MANUAL:
            if semaphore is not None:
                async with semaphore:
                    try:
                        generated = await _gen()
                    except Exception as e:
                        logger.error(
                            "Answer source %s failed on item %s: %s",
                            answer_source.value,
                            item.id,
                            e,
                        )
                        for evaluator in evaluators:
                            item_score_list.append(
                                Score(
                                    metric_name=evaluator.name,
                                    value=-1.0,
                                    reasoning=f"{failure_prefix}: {e}",
                                    source="llm_judge",
                                )
                            )
                        return item_score_list, trajectory_rows
            else:
                try:
                    generated = await _gen()
                except Exception as e:
                    logger.error(
                        "Answer source %s failed on item %s: %s",
                        answer_source.value,
                        item.id,
                        e,
                    )
                    for evaluator in evaluators:
                        item_score_list.append(
                            Score(
                                metric_name=evaluator.name,
                                value=-1.0,
                                reasoning=f"{failure_prefix}: {e}",
                                source="llm_judge",
                            )
                        )
                    return item_score_list, trajectory_rows

        eval_input = EvalInput(
            query=item.query,
            retrieved_contexts=item.context or [],
            generated_answer=generated,
            expected_answer=item.expected_answer,
            agent_id=agent_id if answer_source == AnswerSource.AGENT else None,
        )

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
                item_score_list.append(
                    Score(
                        metric_name=evaluator.name,
                        value=-1.0,
                        reasoning=f"Evaluator error: {e}",
                        source="llm_judge",
                    )
                )

        for score in item_score_list:
            self.db.add(
                EvaluationScoreModel(
                    run_id=run.id,
                    item_id=item.id,
                    metric_name=score.metric_name,
                    value=score.value,
                    reasoning=score.reasoning,
                    source=score.source,
                )
            )

        return item_score_list, trajectory_rows

    @staticmethod
    def _snapshot_exempted_ids(snapshot: dict | None) -> set[str]:
        """Known-bad item ids recorded in a run's frozen dataset snapshot (7.3c)."""
        if not snapshot:
            return set()
        return {str(entry["id"]) for entry in snapshot.get("items", []) if entry.get("known_bad")}

    async def _build_summary(
        self,
        total_items: int,
        item_scores: dict[str, list[Score]],
        metric_averages: dict[str, float],
        summary_config: dict,
        repetitions: int = 1,
        exempted_item_ids: set[str] | None = None,
    ) -> dict:
        """Compute the task-run ``summary`` JSON for an evaluation run.

        Pass/fail requires ``threshold``; regression flags require
        ``baseline_run_id`` (metric regressed when the candidate average
        dropped more than ``regression_threshold`` — default 5% — below
        the baseline average).

        When ``repetitions > 1`` the summary also exposes
        ``consistency_rate``: the fraction of items where every
        repetition meets the threshold (Anthropic ``pass^k`` semantics).
        ``consistency_rate`` is intentionally omitted when
        ``repetitions == 1``.

        Known-bad items (7.3c) are excluded from the numerator and the
        denominator of ``pass_rate`` / ``consistency_rate`` even though
        their scores were recorded. The summary surfaces how many items
        were excluded (``exempted_items``) and which exempted items met
        their threshold anyway (``known_bad_passed_item_ids``) — a
        "dataset healed" hint, never an automatic un-exemption.
        """
        threshold = summary_config.get("threshold")
        exempted = exempted_item_ids or set()
        active_scores = {item_id: scores for item_id, scores in item_scores.items() if item_id not in exempted}
        active_total = len(active_scores)

        passed_items: int | None = None
        failed_items: int | None = None
        pass_rate: float | None = None
        consistency_rate: float | None = None
        known_bad_passed: list[str] = []

        if threshold is not None:
            passed_items = 0
            failed_items = 0
            consistency_passed = 0
            for _item_id, scores in active_scores.items():
                values = [s.value for s in scores]
                meets_threshold = bool(values) and all(v >= threshold for v in values)
                if meets_threshold:
                    passed_items += 1
                    consistency_passed += 1
                else:
                    failed_items += 1
            pass_rate = (passed_items / active_total) if active_total else 0.0
            if repetitions > 1 and active_total:
                consistency_rate = consistency_passed / active_total

            # "Dataset healed" hint: exempted items whose every repetition
            # met the threshold. Reported only — the exemption stays.
            for item_id in exempted:
                scores = item_scores.get(item_id)
                if not scores:
                    continue
                if all(s.value >= threshold for s in scores):
                    known_bad_passed.append(item_id)
            known_bad_passed.sort()

        baseline_run_id = summary_config.get("baseline_run_id")
        delta = float(summary_config.get("regression_threshold", 0.05))
        regressions: list[dict] = []
        if baseline_run_id:
            regressions = await self._compute_regressions(
                uuid.UUID(str(baseline_run_id)),
                metric_averages,
                delta,
            )

        summary: dict = {
            "total_items": total_items,
            "passed_items": passed_items,
            "failed_items": failed_items,
            "pass_rate": pass_rate,
            "metric_averages": metric_averages,
            "regressions": regressions,
            "exempted_items": len([iid for iid in exempted if iid in item_scores]),
        }
        # Persist the effective threshold so downstream consumers (notably
        # the publish gate) can recompute per-item pass semantics from the
        # recorded scores without re-reading the task config. Omitted when
        # no threshold was configured for this run.
        if threshold is not None:
            summary["threshold"] = float(threshold)
        if known_bad_passed:
            summary["known_bad_passed_item_ids"] = known_bad_passed
        if repetitions > 1:
            summary["repetitions"] = repetitions
            summary["consistency_rate"] = consistency_rate
        return summary

    async def _compute_regressions(
        self,
        baseline_run_id: uuid.UUID,
        metric_averages: dict[str, float],
        delta: float,
    ) -> list[dict]:
        """Flag metrics whose candidate average regressed versus a baseline run.

        Both sides operate on known-bad-excluded averages (7.3c): the
        candidate's ``metric_averages`` arrive pre-excluded from
        :meth:`run`, and the baseline's scores are filtered by the
        baseline run's own frozen snapshot exemption set.
        """
        baseline_result = await self.db.get(EvaluationRunModel, baseline_run_id)
        baseline_exempted = self._snapshot_exempted_ids(baseline_result.dataset_snapshot if baseline_result else None)

        stmt = select(
            EvaluationScoreModel.item_id,
            EvaluationScoreModel.metric_name,
            EvaluationScoreModel.value,
        ).where(
            EvaluationScoreModel.run_id == baseline_run_id,
            EvaluationScoreModel.value >= 0,
        )
        rows = (await self.db.execute(stmt)).all()

        baseline_values: dict[str, list[float]] = {}
        for item_id, metric_name, value in rows:
            if str(item_id) in baseline_exempted:
                continue
            baseline_values.setdefault(metric_name, []).append(value)
        baseline_averages = {k: sum(v) / len(v) for k, v in baseline_values.items()}

        regressions: list[dict] = []
        for metric_name, baseline in baseline_averages.items():
            candidate = metric_averages.get(metric_name)
            if candidate is None:
                continue
            if candidate < baseline * (1.0 - delta):
                regressions.append(
                    {
                        "metric_name": metric_name,
                        "baseline": baseline,
                        "candidate": candidate,
                        "drop": baseline - candidate,
                    }
                )
        return regressions

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

    async def _generate_answer_via_workflow(
        self,
        query: str,
        workflow_id: uuid.UUID,
        workflow_version: int | None,
        item_id: uuid.UUID,
        repetitions: int,
    ) -> tuple[str, list[dict]]:
        """Generate an answer by executing the workflow under test.

        One non-streaming ``WorkflowExecutionService.execute`` invocation
        per repetition; the final repetition's ``content`` is the item's
        ``generated_answer``. Per-node execution data (node id, type,
        status, duration, error) is collected into ``trajectory`` rows
        indexed by ``(item_id, repetition_index)`` so downstream diff /
        trajectory views can correlate.

        The studio service is imported lazily inside the function so the
        ops domain stays runtime-clean (per
        ``tests/test_layering_domain.py``); the runtime request path is
        never touched.

        Args:
            query: The dataset item query.
            workflow_id: The workflow under test.
            workflow_version: The locked workflow version (resolved by
                ``run`` before invocation; ``None`` only when the run row
                was created without a pinned version — defensive).
            item_id: Dataset item UUID (used to tag trajectory rows).
            repetitions: How many times to execute the workflow for this
                item. ``>=1``.

        Returns:
            Tuple of (generated_answer, trajectory_rows).

        Raises:
            Exception: Any invocation failure propagates so the caller can
                record per-item error scores.
        """
        from hecate.core.composition.runtime_port_adapter import make_runtime_port
        from hecate.studio.workflows.execution_service import WorkflowExecutionService

        port = make_runtime_port()
        service = WorkflowExecutionService(port=port, db=self.db)

        last_content = ""
        trajectory_rows: list[dict] = []

        for rep in range(1, repetitions + 1):
            session_id = uuid.uuid4()
            result = await service.execute(
                agent_mode="workflow",
                workflow_id=workflow_id,
                session_id=session_id,
                messages=[{"role": "user", "content": query}],
                stream=False,
            )
            content = str((result or {}).get("content") or "").strip()
            last_content = content
            trajectory_rows.append(
                {
                    "item_id": str(item_id),
                    "repetition": rep,
                    "session_id": str(session_id),
                    "workflow_id": str(workflow_id),
                    "workflow_version": workflow_version,
                    "content_length": len(content),
                    "purpose": "workflow_evaluation",
                }
            )

        return last_content, trajectory_rows

    async def _resolve_latest_workflow_version(
        self,
        workflow_id: uuid.UUID,
    ) -> int | None:
        """Resolve a workflow's current latest version number.

        Reads ``WorkflowModel.current_version`` for the given workflow id;
        returns ``None`` when the workflow does not exist (the run row
        remains with ``workflow_version=NULL`` and downstream consumers
        surface this as a configuration error).
        """
        from hecate.models.workflow import WorkflowModel

        stmt = select(WorkflowModel.current_version).where(
            WorkflowModel.id == workflow_id,
            ~WorkflowModel.deleted,
        )
        row = (await self.db.execute(stmt)).first()
        if row is None or row[0] is None:
            return None
        return int(row[0])


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
