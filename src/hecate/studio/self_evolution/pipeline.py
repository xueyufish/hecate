"""Evolution pipeline orchestrator.

One run = harvest-free batch: takes pending learning inputs, runs the
rule pre-filter, LLM attribution, and candidate generation per input, then
validates every touched candidate through the gate and applies the gate
outcome. State machine and lineage live on the EvolutionRunModel.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionRunModel,
    EvolutionRunStatus,
    SkillCandidateModel,
    SkillCandidateStatus,
)
from hecate.studio.self_evolution.attribution import FailureAttributor
from hecate.studio.self_evolution.candidate_generator import CandidateGenerator
from hecate.studio.self_evolution.gate import EvolutionGate, GoldenSubsetBuilder
from hecate.studio.self_evolution.prefilter import TrajectoryPrefilter
from hecate.studio.self_evolution.repository import EvolutionRepository

logger = logging.getLogger(__name__)


class EvolutionPipeline:
    """Runs one batch attribution + validation cycle for a workspace."""

    def __init__(
        self,
        db: AsyncSession,
        event_store: Any | None = None,  # noqa: ANN401
        *,
        gate: EvolutionGate | None = None,
        llm_call_limit: int | None = None,
    ) -> None:
        self._db = db
        self._repo = EvolutionRepository(db)
        self._prefilter = TrajectoryPrefilter()
        self._attributor = FailureAttributor()
        self._generator = CandidateGenerator(db)
        self._gate = gate or EvolutionGate(db)
        self._builder = GoldenSubsetBuilder(db)
        self._event_store = event_store
        self._last_attribution_calls = 0
        if llm_call_limit is None:
            from hecate.core.config import settings

            llm_call_limit = settings.SKILL_EVOLUTION_RUN_LLM_CALL_LIMIT
        self._llm_call_limit = llm_call_limit

    async def run(self, workspace_id: UUID) -> EvolutionRunModel:
        """Execute one run over the workspace's pending learning inputs."""
        run = EvolutionRunModel(workspace_id=workspace_id)
        await self._repo.create_run(run)
        run.status = EvolutionRunStatus.ANALYZING.value

        inputs = await self._repo.list_pending_inputs(workspace_id)
        run.input_count = len(inputs)
        if not inputs:
            run.status = EvolutionRunStatus.CONCLUDED.value
            run.finished_at = _now()
            await self._db.flush()
            return run

        candidates: list[SkillCandidateModel] = []
        agents: set[UUID] = set()

        for row in inputs:
            if run.llm_calls_used >= self._llm_call_limit:
                run.status = EvolutionRunStatus.BUDGET_EXCEEDED.value
                break
            try:
                candidate, agent_id = await self._process_input(workspace_id, run, row)
            except Exception as exc:  # noqa: BLE001 - per-input isolation
                logger.warning("Input %s failed processing: %s", row.id, exc, exc_info=True)
                row.status = "failed"
                row.error = str(exc)[:500]
                continue
            if agent_id is not None:
                agents.add(agent_id)
            if candidate is not None:
                candidates.append(candidate)
            run.llm_calls_used += self._last_attribution_calls

        run.candidate_count = len(candidates)
        if run.status != EvolutionRunStatus.BUDGET_EXCEEDED.value:
            run.status = EvolutionRunStatus.CANDIDATES_READY.value

        for agent_id in agents:
            await self._builder.build(workspace_id, agent_id)

        for candidate in candidates:
            agent_id = await self._candidate_agent(workspace_id, candidate)
            if agent_id is None:
                continue
            report = await self._gate.validate(candidate, workspace_id, agent_id)
            candidate.validation_report = report.to_dict()
            if report.overall == "pass":
                candidate.status = SkillCandidateStatus.VALIDATED.value
            elif report.overall == "insufficient_data":
                candidate.status = SkillCandidateStatus.INSUFFICIENT_DATA.value
            else:
                candidate.status = SkillCandidateStatus.VALIDATION_FAILED.value

        if run.status != EvolutionRunStatus.BUDGET_EXCEEDED.value:
            run.status = EvolutionRunStatus.GATED.value
        run.finished_at = _now()
        await self._db.flush()
        logger.info(
            "Evolution run %s finished: %d inputs, %d candidates, status=%s",
            run.id,
            run.input_count,
            run.candidate_count,
            run.status,
        )
        return run

    async def _process_input(
        self,
        workspace_id: UUID,
        run: EvolutionRunModel,
        row,  # EvolutionInputModel
    ) -> tuple[SkillCandidateModel | None, UUID | None]:
        """Pre-filter → attribute → generate for one learning input."""
        from hecate.ops.ops_center.conversation_messages import (
            project_conversation_messages,
        )

        agent_id = row.agent_id
        if row.conversation_id is None or agent_id is None:
            row.status = "skipped"
            row.error = "missing_conversation_or_agent"
            return None, None

        messages = await project_conversation_messages(self._db, row.conversation_id, self._event_store)
        prefilter = await self._prefilter.evaluate(self._db, row.conversation_id, messages)
        if not prefilter.hit:
            row.status = "skipped"
            return None, agent_id

        attribution = await self._attributor.attribute(messages, prefilter.patterns, [])
        run.tokens_used += attribution.tokens_used
        self._last_attribution_calls = attribution.llm_calls

        candidate = await self._generator.generate_or_update(
            workspace_id=workspace_id,
            run_id=run.id,
            source_input=row,
            attribution=attribution,
        )
        return candidate, agent_id

    async def _candidate_agent(self, workspace_id: UUID, candidate: SkillCandidateModel) -> UUID | None:
        """Resolve the agent a candidate belongs to, via its source inputs."""
        for delta in candidate.deltas or []:
            raw = delta.get("source_input_id") if isinstance(delta, dict) else None
            if raw is None:
                continue
            row = await self._repo.get_input(workspace_id, UUID(str(raw)))
            if row is not None and row.agent_id is not None:
                return row.agent_id
        return None


def _now():
    """Current UTC timestamp."""
    from datetime import UTC, datetime

    return datetime.now(UTC)
