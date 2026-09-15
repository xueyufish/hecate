"""Background execution of prompt self-optimization runs (6.19).

Job-runner shape mirrors the offline evaluation task runner: the API
commits a ``created`` run row, hands off here, and a fresh session drives
the loop:

1. Freeze inputs — the pinned dataset version's items are split
   train/validation with a run-seeded shuffle.
2. Baseline — the base template is rolled out (via ``prompt_override``) on
   both splits; validation averages become the gate reference, train
   failures become the first mutation evidence. Baseline rollouts bypass
   the agent's persona on purpose so deltas measure template vs template.
3. Rounds — reflect on the parent's failures to propose a candidate
   (mutation call), gate its template integrity (no rollout cost on
   failure), roll it out on train + validation, apply the acceptance gate,
   scan accepted candidates into the review pool, and update the
   Pareto-lite pool.
4. Stop — budget exhausted, max rounds, N consecutive rounds without a
   gate-passing candidate, or user cancellation (observed on a fresh
   session at round boundaries).

Every rollout writes a trace row attributed to the run/candidate so
optimization traffic stays separable from production prompt analytics.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetVersionModel,
    EvaluationRunModel,
    RunStatus,
)
from hecate.models.prompt_optimization import (
    NO_PROGRESS_ROUNDS,
    PromptOptimizationCandidateModel,
    PromptOptimizationCandidateStatus,
    PromptOptimizationRunModel,
    PromptOptimizationRunStatus,
    PromptOptimizationStopReason,
)
from hecate.models.trace import SpanStatus, TraceModel
from hecate.ops.evaluation.engine import EvaluationEngine, get_evaluator_class
from hecate.ops.evaluation.tasks.runner import _items_from_snapshot
from hecate.ops.evaluation.types import AnswerSource
from hecate.ops.prompt_optimization.gates import (
    check_template_integrity,
    evaluate_acceptance,
    gate_report_dict,
    metric_sources,
)
from hecate.ops.prompt_optimization.scanner import PromptCandidateScanner
from hecate.ops.prompt_optimization.strategy import MutationStrategy, ReflectionError

logger = logging.getLogger(__name__)

_STRATEGIES: dict[str, type[MutationStrategy]] = {}


def register_strategy(strategy: MutationStrategy) -> None:
    """Register a strategy implementation under its config name."""
    _STRATEGIES[strategy.name] = strategy


def _default_strategies() -> dict[str, MutationStrategy]:
    from hecate.ops.prompt_optimization.strategy import ReflectiveMutationStrategy

    return {"reflective_mutation": ReflectiveMutationStrategy()}


@dataclass
class _RolloutOverride:
    """Duck-typed per-invocation override for rollout ``agent_execute`` calls.

    Only ``prompt_override`` is set: tools, knowledge bases, and model
    configuration keep resolving from the agent under test. A local
    dataclass (rather than importing runtime's AgentDefinition) keeps the
    ops package free of runtime imports.
    """

    prompt_override: str


class _BudgetExhaustedError(Exception):
    """Internal: rollout budget ran out mid-round."""


class PromptOptimizationRunner:
    """Execute optimization runs in the background.

    Args:
        db: Request-scoped session (unused by the background loop, which
            opens its own session via ``async_session_factory`` — same
            handoff contract as the evaluation task runner).
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    async def run_in_background(self, run_id: uuid.UUID) -> None:
        """Spawn an asyncio task executing the run; returns immediately."""
        asyncio.create_task(self._execute(run_id))

    async def _execute(self, run_id: uuid.UUID) -> None:
        from hecate.core.database import async_session_factory  # local import — avoid circular

        if async_session_factory is None:
            logger.error("async_session_factory unavailable — cannot run optimization run %s", run_id)
            return

        async with async_session_factory() as session:
            run = await session.get(PromptOptimizationRunModel, run_id)
            if run is None:
                logger.error("Optimization run %s not found", run_id)
                return
            if run.status not in (PromptOptimizationRunStatus.CREATED.value,):
                logger.warning("Optimization run %s in status %s — not starting", run_id, run.status)
                return

            run.status = PromptOptimizationRunStatus.RUNNING.value
            run.started_at = datetime.now(UTC)
            usage: dict = {"rollout_items": 0, "mutation_calls": 0, "rollout_tokens": 0, "eval_runs": 0}
            run.usage = usage
            await session.flush()

            try:
                stop_reason = await self._loop(run, session, usage)
                run.usage = usage
                if run.stop_reason == PromptOptimizationStopReason.CANCELLED.value:
                    stop_reason = PromptOptimizationStopReason.CANCELLED.value
                run.stop_reason = stop_reason.value
                run.round_count = usage.get("rounds", run.round_count)
                await self._finalize(run, session)
                await session.commit()
            except Exception:
                logger.exception("Optimization run %s failed", run_id)
                run.status = PromptOptimizationRunStatus.FAILED.value
                run.error = "internal error — see logs"
                run.completed_at = datetime.now(UTC)
                await session.commit()

    # --- loop -------------------------------------------------------------

    async def _loop(
        self,
        run: PromptOptimizationRunModel,
        session: AsyncSession,
        usage: dict,
    ) -> PromptOptimizationStopReason:
        prompt_row = await self._load_base_template(run, session)
        base_template, base_variables = prompt_row

        version = await session.get(EvaluationDatasetVersionModel, run.dataset_version_id)
        if version is None or version.deleted:
            msg = f"dataset version {run.dataset_version_id} vanished"
            raise RuntimeError(msg)
        items = _items_from_snapshot(list(version.items or []), run.dataset_id)
        train, val = self._split(items, run.split_ratio, run.id)

        evaluators = []
        for name in run.evaluator_configs or []:
            cls = get_evaluator_class(name)
            if cls is not None:
                evaluators.append(cls())
        if not evaluators:
            msg = "no evaluators could be resolved"
            raise RuntimeError(msg)

        strategies = _default_strategies()
        strategy = strategies.get(run.strategy)
        if strategy is None:
            msg = f"unknown strategy: {run.strategy}"
            raise RuntimeError(msg)

        engine = EvaluationEngine(session)

        baseline_val = await self._rollout(
            engine, run, session, evaluators, val, base_template, "baseline_validation", None, usage
        )
        baseline_train = await self._rollout(
            engine, run, session, evaluators, train, base_template, "baseline_train", None, usage
        )
        baseline_avg_val = baseline_val["metric_averages"]
        if run.primary_metric not in baseline_avg_val:
            msg = f"primary metric {run.primary_metric} produced no baseline scores"
            raise RuntimeError(msg)
        sources = metric_sources(baseline_val["item_scores"])
        usage["baseline"] = {
            "validation": {
                "metric_averages": baseline_avg_val,
                "eval_run_id": baseline_val["eval_run_id"],
            },
            "train": {
                "metric_averages": baseline_train["metric_averages"],
                "eval_run_id": baseline_train["eval_run_id"],
            },
        }
        await session.flush()

        baseline_primary = baseline_avg_val[run.primary_metric]
        pool: list[tuple[uuid.UUID, str, dict[str, float]]] = []
        parent_template = base_template
        evidence = self._failure_evidence(baseline_train, baseline_primary, run.primary_metric)
        no_progress = 0

        for round_no in range(1, run.max_rounds + 1):
            usage["rounds"] = round_no
            run.round_count = round_no
            await session.flush()

            if await self._cancel_requested(run.id):
                return PromptOptimizationStopReason.CANCELLED
            planned_items = len(train) + len(val)
            if usage["rollout_items"] + planned_items > run.rollout_item_limit:
                return PromptOptimizationStopReason.BUDGET_EXHAUSTED
            if usage["mutation_calls"] + 1 > run.mutation_call_limit:
                return PromptOptimizationStopReason.BUDGET_EXHAUSTED

            usage["mutation_calls"] += 1
            try:
                proposal = await strategy.mutate(base_template, parent_template, evidence, run.reflection_model)
            except ReflectionError as e:
                await self._record_candidate(
                    session,
                    run,
                    round_no=round_no,
                    template="",
                    status=PromptOptimizationCandidateStatus.REJECTED_MUTATION,
                    reflection_summary=None,
                    rejection_reason=f"reflection_error: {e}",
                )
                no_progress += 1
                if no_progress >= NO_PROGRESS_ROUNDS:
                    return PromptOptimizationStopReason.NO_PROGRESS
                continue

            integrity = check_template_integrity(proposal.template, base_variables)
            if not integrity.passed:
                await self._record_candidate(
                    session,
                    run,
                    round_no=round_no,
                    template=proposal.template,
                    status=PromptOptimizationCandidateStatus.REJECTED_MUTATION,
                    reflection_summary=proposal.summary,
                    rejection_reason=integrity.reason,
                )
                no_progress += 1
                if no_progress >= NO_PROGRESS_ROUNDS:
                    return PromptOptimizationStopReason.NO_PROGRESS
                continue

            candidate = await self._record_candidate(
                session,
                run,
                round_no=round_no,
                template=proposal.template,
                status=PromptOptimizationCandidateStatus.REJECTED_MUTATION,
                reflection_summary=proposal.summary,
                rejection_reason=None,
            )

            train_res = await self._rollout(
                engine,
                run,
                session,
                evaluators,
                train,
                proposal.template,
                f"round{round_no}_train",
                candidate.id,
                usage,
            )
            val_res = await self._rollout(
                engine,
                run,
                session,
                evaluators,
                val,
                proposal.template,
                f"round{round_no}_validation",
                candidate.id,
                usage,
            )

            gate = evaluate_acceptance(
                baseline_avg_val,
                val_res["metric_averages"],
                run.primary_metric,
                run.min_improvement,
                run.max_regression,
                sources,
            )
            candidate.gate_report = gate_report_dict(gate)
            candidate.per_item_results = train_res["per_item"] + val_res["per_item"]

            if gate.accepted:
                verdict = await PromptCandidateScanner().scan(proposal.template)
                if verdict.status == "blocked":
                    candidate.status = PromptOptimizationCandidateStatus.SCAN_BLOCKED.value
                    candidate.gate_report["scan"] = verdict.findings
                    no_progress += 1
                else:
                    candidate.status = PromptOptimizationCandidateStatus.PENDING_REVIEW.value
                    pool.append((candidate.id, proposal.template, val_res["metric_averages"]))
                    no_progress = 0
            else:
                candidate.status = PromptOptimizationCandidateStatus.GATE_REJECTED.value
                no_progress += 1
            await session.flush()

            if no_progress >= NO_PROGRESS_ROUNDS:
                return PromptOptimizationStopReason.NO_PROGRESS
            parent_template = self._next_parent(pool, parent_template, run.primary_metric)
            evidence = self._failure_evidence(train_res, baseline_primary, run.primary_metric)

        return PromptOptimizationStopReason.MAX_ROUNDS

    # --- helpers ----------------------------------------------------------

    async def _load_base_template(
        self,
        run: PromptOptimizationRunModel,
        session: AsyncSession,
    ) -> tuple[str, list[str]]:
        from sqlalchemy import select

        from hecate.models.prompt import PromptVersionModel
        from hecate.studio.template_engine import TemplateEngine

        result = await session.execute(
            select(PromptVersionModel).where(
                PromptVersionModel.prompt_id == run.prompt_id,
                PromptVersionModel.version == run.base_version,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            msg = f"base prompt version {run.base_version} vanished"
            raise RuntimeError(msg)
        variables = list(row.variables or [])
        if not variables:
            variables = TemplateEngine().extract_variables(row.template)
        return row.template, variables

    def _split(
        self,
        items: list,
        ratio: float,
        seed: uuid.UUID,
    ) -> tuple[list, list]:
        ordered = list(items)
        # Deterministic split shuffling (reproducibility), not security.
        rng = random.Random(seed.int & 0xFFFFFFFF)  # noqa: S311
        rng.shuffle(ordered)
        cut = round(len(ordered) * ratio)
        cut = max(1, min(len(ordered) - 1, cut))
        return ordered[:cut], ordered[cut:]

    async def _rollout(
        self,
        engine: EvaluationEngine,
        run: PromptOptimizationRunModel,
        session: AsyncSession,
        evaluators: list,
        items: list,
        template: str,
        phase: str,
        candidate_id: uuid.UUID | None,
        usage: dict,
    ) -> dict:
        eval_run = EvaluationRunModel(
            dataset_id=run.dataset_id,
            dataset_version_id=run.dataset_version_id,
            status=RunStatus.PENDING.value,
            evaluator_configs=[e.name for e in evaluators],
            workspace_id=run.workspace_id,
        )
        session.add(eval_run)
        await session.flush()

        capture: list[dict] = []
        result = await engine.run(
            evaluators,
            run.dataset_id,
            answer_source=AnswerSource.AGENT,
            agent_id=run.agent_id,
            run=eval_run,
            items_override=items,
            agent_definition=_RolloutOverride(prompt_override=template),
            rollout_capture=capture,
        )
        usage["rollout_items"] += len(items)
        usage["eval_runs"] += 1
        usage["rollout_tokens"] += sum(int(entry.get("usage", {}).get("total_tokens") or 0) for entry in capture)
        per_item = self._merge_per_item(items, result.item_scores, capture)
        await self._write_trace(session, run, candidate_id, phase, len(items), result.metric_averages)
        return {
            "metric_averages": dict(result.metric_averages),
            "item_scores": result.item_scores,
            "per_item": per_item,
            "eval_run_id": str(eval_run.id),
        }

    def _merge_per_item(self, items: list, item_scores: dict[str, list], capture: list[dict]) -> list[dict]:
        generated_by_item = {entry["item_id"]: entry for entry in capture if entry.get("item_id")}
        merged: list[dict] = []
        for item in items:
            key = str(item.id)
            scores = item_scores.get(key, [])
            generated = generated_by_item.get(key, {}).get("generated")
            merged.append(
                {
                    "item_id": key,
                    "query": item.query,
                    "expected_answer": item.expected_answer,
                    "generated": generated,
                    "scores": [
                        {
                            "metric": s.metric_name,
                            "value": s.value,
                            "reasoning": s.reasoning,
                            "source": s.source,
                        }
                        for s in scores
                    ],
                }
            )
        return merged

    def _failure_evidence(
        self,
        rollout_res: dict,
        baseline_primary: float,
        primary_metric: str,
    ) -> list[dict]:
        """Failure trajectories for the reflection call: items scoring below
        the baseline's primary average (or erroring outright)."""
        evidence: list[dict] = []
        for entry in rollout_res["per_item"]:
            scores = {s["metric"]: s for s in entry["scores"]}
            primary = scores.get(primary_metric)
            if primary is None:
                continue
            if primary["value"] < baseline_primary or primary["value"] < 0:
                evidence.append(
                    {
                        "query": entry["query"],
                        "expected_answer": entry["expected_answer"],
                        "generated": entry["generated"],
                        "scores": entry["scores"],
                    }
                )
        return evidence

    def _next_parent(
        self,
        pool: list[tuple[uuid.UUID, str, dict[str, float]]],
        fallback_template: str,
        primary_metric: str,
    ) -> str:
        """Pick the pool candidate with the highest validation primary score
        (Pareto-lite current_best); an empty pool keeps the current parent."""
        best_template: str | None = None
        best_score = -1.0
        for _cid, template, val_avg in pool:
            score = val_avg.get(primary_metric, -1.0)
            if score >= best_score:
                best_score = score
                best_template = template
        return best_template if best_template is not None else fallback_template

    async def _record_candidate(
        self,
        session: AsyncSession,
        run: PromptOptimizationRunModel,
        round_no: int,
        template: str,
        status: PromptOptimizationCandidateStatus,
        reflection_summary: str | None,
        rejection_reason: str | None,
    ) -> PromptOptimizationCandidateModel:
        candidate = PromptOptimizationCandidateModel(
            run_id=run.id,
            round_no=round_no,
            template=template,
            status=status.value,
            reflection_summary=reflection_summary,
            rejection_reason=rejection_reason,
            workspace_id=run.workspace_id,
        )
        session.add(candidate)
        await session.flush()
        return candidate

    async def _write_trace(
        self,
        session: AsyncSession,
        run: PromptOptimizationRunModel,
        candidate_id: uuid.UUID | None,
        phase: str,
        item_count: int,
        metric_averages: dict[str, float],
    ) -> None:
        now = datetime.now(UTC)
        session.add(
            TraceModel(
                trace_id=uuid.uuid4(),
                type="prompt_optimization",
                name=f"rollout:{phase}",
                agent_id=run.agent_id,
                metadata_={
                    "optimization_run_id": str(run.id),
                    "candidate_id": str(candidate_id) if candidate_id else None,
                    "prompt_id": str(run.prompt_id),
                    "prompt_version": run.base_version,
                    "phase": phase,
                    "item_count": item_count,
                    "metric_averages": metric_averages,
                },
                status=SpanStatus.COMPLETED.value,
                start_time=now,
                end_time=now,
            )
        )
        await session.flush()

    async def _cancel_requested(self, run_id: uuid.UUID) -> bool:
        """Read the cancellation flag on a fresh session so an API-side
        cancel commit becomes visible even though this loop holds its own
        long-lived session."""
        from hecate.core.database import async_session_factory

        if async_session_factory is None:
            return False
        async with async_session_factory() as session:
            row = await session.get(PromptOptimizationRunModel, run_id)
            return bool(
                row
                and row.status == PromptOptimizationRunStatus.RUNNING.value
                and row.stop_reason == PromptOptimizationStopReason.CANCELLED.value
            )

    async def _finalize(
        self,
        run: PromptOptimizationRunModel,
        session: AsyncSession,
    ) -> None:
        from sqlalchemy import select

        pending_stmt = select(PromptOptimizationCandidateModel.id).where(
            PromptOptimizationCandidateModel.run_id == run.id,
            PromptOptimizationCandidateModel.status == PromptOptimizationCandidateStatus.PENDING_REVIEW.value,
            ~PromptOptimizationCandidateModel.deleted,
        )
        has_pending = (await session.execute(pending_stmt)).first() is not None
        run.status = (
            PromptOptimizationRunStatus.AWAITING_REVIEW.value
            if has_pending
            else PromptOptimizationRunStatus.CONCLUDED.value
        )
        run.completed_at = datetime.now(UTC)
