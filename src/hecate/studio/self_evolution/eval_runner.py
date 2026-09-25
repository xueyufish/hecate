"""Production eval_runner adapter for the evolution gate (1.3.6f follow-up).

Bridges :class:`~hecate.studio.self_evolution.gate.EvolutionGate` to the
7.2c offline evaluation infrastructure. When the agent under test has an
active OFFLINE evaluation task bound (``answer_source="agent"``), each gate
leg executes one synchronous ``EvaluationEngine.run`` pass — with the
candidate skill injected via the ``extra_skill_instructions`` per-invocation
seam for ``bind_skill=True``, or with the agent's exact production behavior
for ``bind_skill=False`` — and returns the run's ``pass_rate``.

Returns ``None`` (the gate records the behavioral checks as skipped) when:

- no active offline task is bound to the agent (the opt-in signal — no
  bound task means the deployment has not asked for behavioral gating);
- the bound task is not agent-answer-sourced;
- the cost guardrail rejects the run or any execution error occurs.

Run rows created here carry the task id, so gate evaluations are visible
in the task's run history alongside API-triggered runs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class CandidateSkillBinding:
    """Per-invocation agent_definition carrying one candidate skill.

    Read by the runtime port via ``getattr`` (same pattern as
    ``prompt_override``): ``extra_skill_instructions`` is appended to the
    agent's skill block; ``tools`` stays ``None`` so the agent's configured
    tool surface is untouched.
    """

    extra_skill_instructions: str
    tools: Any = None


class OfflineEvaluationRunner:
    """Gate ``eval_runner`` over the agent's bound 7.2c offline task."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def __call__(self, candidate: Any, *, bind_skill: bool, agent_id: uuid.UUID) -> float | None:
        """One behavioral-evaluation leg. Returns pass_rate, or None when unrunnable."""
        try:
            return await self._run_leg(candidate, bind_skill=bind_skill, agent_id=agent_id)
        except Exception:
            logger.warning(
                "Offline eval leg failed (candidate=%s bind_skill=%s)",
                getattr(candidate, "id", None),
                bind_skill,
                exc_info=True,
            )
            return None

    async def _run_leg(self, candidate: Any, *, bind_skill: bool, agent_id: uuid.UUID) -> float | None:
        from hecate.models.evaluation import EvaluationRunModel, RunStatus
        from hecate.ops.evaluation.engine import EvaluationEngine
        from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner

        task = await self._resolve_task(candidate.workspace_id, agent_id)
        if task is None:
            return None

        config = task.config or {}
        answer_source = str(config.get("answer_source") or "manual")
        if answer_source != "agent":
            logger.info("Bound eval task %s is not agent-sourced; skipping behavioral leg", task.id)
            return None

        runner = OfflineTaskRunner(self._db)
        evaluators = runner._resolve_evaluators(task)
        if not evaluators:
            logger.info("Bound eval task %s resolved no evaluators; skipping behavioral leg", task.id)
            return None

        repetitions = int(config.get("repetitions") or 1)
        max_total = int(config.get("max_total_executions") or 1000)
        dataset_id = uuid.UUID(str(config["dataset_id"]))
        item_count = await runner._count_items(dataset_id)
        if item_count * repetitions > max_total:
            logger.info(
                "Bound eval task %s cost guardrail (%d x %d > %d); skipping behavioral leg",
                task.id,
                item_count,
                repetitions,
                max_total,
            )
            return None

        run = EvaluationRunModel(
            dataset_id=dataset_id,
            task_id=task.id,
            evaluator_configs=list(task.evaluator_configs or []),
            status=RunStatus.PENDING.value,
            workspace_id=task.workspace_id,
        )
        self._db.add(run)
        await self._db.flush()

        engine = EvaluationEngine(self._db)
        result = await engine.run(
            evaluators=evaluators,
            dataset_id=dataset_id,
            answer_source=answer_source,
            tags=config.get("tags"),
            agent_id=agent_id,
            run=run,
            summary_config=runner._summary_config(task),
            repetitions=repetitions,
            agent_definition=self._binding(candidate) if bind_skill else None,
        )

        summary = getattr(run, "summary", None)
        pass_rate = summary.get("pass_rate") if isinstance(summary, dict) else None
        if pass_rate is None:
            # No threshold summary on the task — fall back to the primary
            # metric average so the with/without comparison still has signal.
            pass_rate = _primary_metric_average(result)
        logger.info(
            "Behavioral leg done (candidate=%s bind_skill=%s task=%s run=%s pass_rate=%s)",
            getattr(candidate, "id", None),
            bind_skill,
            task.id,
            result.run_id,
            pass_rate,
        )
        return float(pass_rate) if pass_rate is not None else None

    async def _resolve_task(self, workspace_id: Any, agent_id: uuid.UUID) -> Any:
        """The agent's newest active offline task (bound = task.config.agent_id)."""
        from sqlalchemy import select

        from hecate.models.evaluation import EvaluationTaskModel, TaskStatus, TaskType

        rows = (
            (
                await self._db.execute(
                    select(EvaluationTaskModel)
                    .where(
                        EvaluationTaskModel.workspace_id == workspace_id,
                        EvaluationTaskModel.task_type == TaskType.OFFLINE.value,
                        EvaluationTaskModel.status == TaskStatus.ACTIVE.value,
                        ~EvaluationTaskModel.deleted,
                    )
                    .order_by(EvaluationTaskModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for task in rows:
            config = task.config or {}
            raw = config.get("agent_id")
            try:
                if raw and uuid.UUID(str(raw)) == agent_id:
                    return task
            except ValueError:
                continue
        return None

    def _binding(self, candidate: Any) -> CandidateSkillBinding:
        """Render the candidate exactly as review publication composes it."""
        instructions = f"## Procedure\n\n{candidate.procedure}\n\n## Guardrails\n\n{candidate.guardrails}"
        return CandidateSkillBinding(extra_skill_instructions=instructions)


def _primary_metric_average(result: Any) -> float | None:
    """A single comparable score from a run result without a threshold summary.

    Prefers an explicit ``pass_rate`` metric; otherwise the single-metric
    average. Multi-metric tasks without pass_rate return None — averaging
    across heterogeneous metrics would be meaningless.
    """
    averages: dict[str, float] = getattr(result, "metric_averages", None) or {}
    if not averages:
        return None
    if "pass_rate" in averages:
        return averages["pass_rate"]
    if len(averages) == 1:
        return next(iter(averages.values()))
    return None
