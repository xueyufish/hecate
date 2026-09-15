"""Human review and publication for optimization candidates (6.19).

Gate-passing, scan-clean candidates wait in ``pending_review``. Approval
publishes the candidate as a new immutable prompt version (auto-generated
commit message + provenance metadata, no labels — the existing label flow
governs deployment). Rejection requires a reason. The run concludes when
every candidate holds a terminal decision. Nothing publishes without an
explicit human action, and nothing is ever deleted.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.prompt import PromptModel, PromptVersionModel
from hecate.models.prompt_optimization import (
    TERMINAL_CANDIDATE_STATUSES,
    PromptOptimizationCandidateModel,
    PromptOptimizationCandidateStatus,
    PromptOptimizationRunModel,
    PromptOptimizationRunStatus,
)

logger = logging.getLogger(__name__)


class CandidateReviewError(ValueError):
    """The candidate is not in a state that allows the requested decision."""


class CandidateReviewService:
    """Apply reviewer decisions to candidates and conclude runs."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_candidate(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> PromptOptimizationCandidateModel | None:
        candidate = await self.db.get(PromptOptimizationCandidateModel, candidate_id)
        if candidate is None or candidate.deleted or candidate.workspace_id != workspace_id:
            return None
        return candidate

    async def list_candidates(
        self,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> list[PromptOptimizationCandidateModel]:
        stmt = (
            select(PromptOptimizationCandidateModel)
            .where(
                PromptOptimizationCandidateModel.run_id == run_id,
                PromptOptimizationCandidateModel.workspace_id == workspace_id,
                ~PromptOptimizationCandidateModel.deleted,
            )
            .order_by(
                PromptOptimizationCandidateModel.round_no.asc(),
                PromptOptimizationCandidateModel.created_at.asc(),
            )
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def approve(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
        decided_by: uuid.UUID | None = None,
    ) -> tuple[PromptOptimizationCandidateModel, PromptVersionModel]:
        """Publish an approved candidate as a new prompt version.

        Returns:
            The updated candidate and the newly created prompt version.

        Raises:
            CandidateReviewError: The candidate is not ``pending_review``.
            LookupError: The candidate's prompt vanished.
        """
        candidate = await self._require_candidate(
            candidate_id, workspace_id, PromptOptimizationCandidateStatus.PENDING_REVIEW
        )
        run = await self.db.get(PromptOptimizationRunModel, candidate.run_id)
        if run is None:
            msg = f"run {candidate.run_id} vanished"
            raise LookupError(msg)
        prompt = await self._get_prompt(run.prompt_id, workspace_id)
        if prompt is None:
            msg = f"prompt {run.prompt_id} not found"
            raise LookupError(msg)

        # Lazy: studio is a sibling domain — a module-level import would
        # violate the ops layering rule (same pattern as runtime→studio).
        from hecate.studio.template_engine import TemplateEngine

        engine = TemplateEngine()
        engine.validate(candidate.template)
        variables = engine.extract_variables(candidate.template)

        new_version_num = prompt.current_version + 1
        primary = run.primary_metric
        base_score = (candidate.gate_report or {}).get("baseline_scores", {}).get(primary)
        cand_score = (candidate.gate_report or {}).get("candidate_scores", {}).get(primary)
        delta = f"{cand_score - base_score:+.3f}" if base_score is not None and cand_score is not None else "n/a"
        version = PromptVersionModel(
            prompt_id=prompt.id,
            version=new_version_num,
            template=candidate.template,
            variables=variables,
            labels=[],
            commit_message=(
                f"prompt-optimization: run {run.id} candidate {candidate.id} — "
                f"{primary} {delta} vs baseline v{run.base_version}"
            ),
            metadata_={
                "source": "prompt_optimization",
                "run_id": str(run.id),
                "candidate_id": str(candidate.id),
                "base_version": run.base_version,
                "baseline_scores": (candidate.gate_report or {}).get("baseline_scores", {}),
                "candidate_scores": (candidate.gate_report or {}).get("candidate_scores", {}),
            },
            workspace_id=workspace_id,
        )
        self.db.add(version)
        prompt.current_version = new_version_num

        candidate.status = PromptOptimizationCandidateStatus.PUBLISHED.value
        candidate.decided_by = decided_by
        candidate.decided_at = datetime.now(UTC)
        await self.db.flush()
        await self._maybe_conclude(run)
        logger.info("Published optimization candidate %s as prompt version %s", candidate.id, new_version_num)
        return candidate, version

    async def reject(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
        reason: str,
        decided_by: uuid.UUID | None = None,
    ) -> PromptOptimizationCandidateModel:
        """Reject a pending candidate; the reason is mandatory and persisted."""
        candidate = await self._require_candidate(
            candidate_id, workspace_id, PromptOptimizationCandidateStatus.PENDING_REVIEW
        )
        run = await self.db.get(PromptOptimizationRunModel, candidate.run_id)
        candidate.status = PromptOptimizationCandidateStatus.REJECTED.value
        candidate.rejection_reason = reason
        candidate.decided_by = decided_by
        candidate.decided_at = datetime.now(UTC)
        await self.db.flush()
        if run is not None:
            await self._maybe_conclude(run)
        return candidate

    # --- helpers ----------------------------------------------------------

    async def _require_candidate(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
        expected: PromptOptimizationCandidateStatus,
    ) -> PromptOptimizationCandidateModel:
        candidate = await self.get_candidate(candidate_id, workspace_id)
        if candidate is None:
            msg = f"candidate {candidate_id} not found"
            raise LookupError(msg)
        if candidate.status != expected.value:
            msg = f"candidate {candidate_id} is {candidate.status}, expected {expected.value}"
            raise CandidateReviewError(msg)
        return candidate

    async def _maybe_conclude(self, run: PromptOptimizationRunModel) -> None:
        if run.status != PromptOptimizationRunStatus.AWAITING_REVIEW.value:
            return
        undecided = [
            c
            for c in await self.list_candidates(run.id, run.workspace_id)
            if PromptOptimizationCandidateStatus(c.status) not in TERMINAL_CANDIDATE_STATUSES
        ]
        if not undecided:
            run.status = PromptOptimizationRunStatus.CONCLUDED.value
            run.completed_at = datetime.now(UTC)
            await self.db.flush()

    async def _get_prompt(self, prompt_id: uuid.UUID, workspace_id: uuid.UUID) -> PromptModel | None:
        from sqlalchemy import select

        result = await self.db.execute(
            select(PromptModel).where(
                PromptModel.id == prompt_id,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
        )
        return result.scalar_one_or_none()
