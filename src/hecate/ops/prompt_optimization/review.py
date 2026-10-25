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

from sqlalchemy import select, update
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

    async def get_candidate_binding(
        self, candidate_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Resolve visible run/prompt IDs without loading candidate evidence."""
        row = (
            await self.db.execute(
                select(PromptOptimizationCandidateModel.run_id, PromptOptimizationRunModel.prompt_id)
                .join(
                    PromptOptimizationRunModel, PromptOptimizationRunModel.id == PromptOptimizationCandidateModel.run_id
                )
                .where(
                    PromptOptimizationCandidateModel.id == candidate_id,
                    PromptOptimizationCandidateModel.workspace_id == workspace_id,
                    ~PromptOptimizationCandidateModel.deleted,
                    PromptOptimizationRunModel.workspace_id == workspace_id,
                    ~PromptOptimizationRunModel.deleted,
                )
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def get_candidate(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> PromptOptimizationCandidateModel | None:
        """A live candidate whose parent run is also live in the workspace.

        A candidate whose own workspace matches but whose parent run was
        deleted or moved is invisible — reading it would leak another
        run's evidence.
        """
        candidate = await self._fresh_candidate(candidate_id, workspace_id)
        if candidate is None:
            return None
        if await self._live_run(candidate.run_id, workspace_id) is None:
            return None
        return candidate

    async def list_candidates(
        self,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> list[PromptOptimizationCandidateModel]:
        """Live candidates of one live run, excluding binding inconsistencies."""
        if await self._live_run(run_id, workspace_id) is None:
            raise LookupError(f"run {run_id} not found in workspace")
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
            .execution_options(populate_existing=True)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def approve(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
        decided_by: uuid.UUID | None = None,
        *,
        expected_binding: tuple[uuid.UUID, uuid.UUID] | None = None,
    ) -> tuple[PromptOptimizationCandidateModel, PromptVersionModel]:
        """Publish an approved candidate as a new prompt version.

        The candidate→run→prompt chain is re-verified with fresh reads
        inside this transaction — a stale API precheck or a post-precheck
        rebinding cannot publish to another object.

        Returns:
            The updated candidate and the newly created prompt version.

        Raises:
            CandidateReviewError: The candidate is not ``pending_review``.
            LookupError: The candidate's prompt vanished.
        """
        candidate, run = await self._require_candidate(
            candidate_id, workspace_id, PromptOptimizationCandidateStatus.PENDING_REVIEW, expected_binding
        )
        prompt = await self._get_prompt(run.prompt_id, workspace_id)
        if prompt is None:
            raise LookupError("candidate not found")

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
        *,
        expected_binding: tuple[uuid.UUID, uuid.UUID] | None = None,
    ) -> PromptOptimizationCandidateModel:
        """Reject a pending candidate; the reason is mandatory and persisted.

        The candidate→run binding is re-verified with fresh reads before
        any mutation: an invisible or inconsistent chain rejects nothing —
        the candidate, run, and prompt stay exactly as they were.
        """
        candidate, run = await self._require_candidate(
            candidate_id, workspace_id, PromptOptimizationCandidateStatus.PENDING_REVIEW, expected_binding
        )
        candidate.status = PromptOptimizationCandidateStatus.REJECTED.value
        candidate.rejection_reason = reason
        candidate.decided_by = decided_by
        candidate.decided_at = datetime.now(UTC)
        await self.db.flush()
        await self._maybe_conclude(run)
        return candidate

    # --- helpers ----------------------------------------------------------

    async def _fresh_candidate(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> PromptOptimizationCandidateModel | None:
        """Fresh read of a live candidate in the workspace (identity-map safe)."""
        stmt = (
            select(PromptOptimizationCandidateModel)
            .where(
                PromptOptimizationCandidateModel.id == candidate_id,
                PromptOptimizationCandidateModel.workspace_id == workspace_id,
                ~PromptOptimizationCandidateModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _live_run(
        self,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> PromptOptimizationRunModel | None:
        """Fresh read of the parent run when it is live in the workspace."""
        stmt = (
            select(PromptOptimizationRunModel)
            .where(
                PromptOptimizationRunModel.id == run_id,
                PromptOptimizationRunModel.workspace_id == workspace_id,
                ~PromptOptimizationRunModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _require_candidate(
        self,
        candidate_id: uuid.UUID,
        workspace_id: uuid.UUID,
        expected: PromptOptimizationCandidateStatus,
        expected_binding: tuple[uuid.UUID, uuid.UUID] | None = None,
    ) -> tuple[PromptOptimizationCandidateModel, PromptOptimizationRunModel]:
        candidate = await self._fresh_candidate(candidate_id, workspace_id, for_update=True)
        if candidate is None:
            raise LookupError("candidate not found")
        run = await self._live_run(candidate.run_id, workspace_id, for_update=True)
        if run is None or (expected_binding is not None and expected_binding != (candidate.run_id, run.prompt_id)):
            raise LookupError("candidate not found")
        if candidate.status != expected.value:
            msg = f"candidate {candidate_id} is {candidate.status}, expected {expected.value}"
            raise CandidateReviewError(msg)
        binding = (candidate.run_id, run.prompt_id)
        # SQLite ignores FOR UPDATE. Claim the unchanged binding with a
        # conditional no-op write before deciding, acquiring its writer
        # lock without changing timestamps or leaving a rejected decision.
        live_parent = (
            select(PromptOptimizationRunModel.id)
            .where(
                PromptOptimizationRunModel.id == binding[0],
                PromptOptimizationRunModel.prompt_id == binding[1],
                PromptOptimizationRunModel.workspace_id == workspace_id,
                ~PromptOptimizationRunModel.deleted,
            )
            .exists()
        )
        claimed = (
            await self.db.execute(
                update(PromptOptimizationCandidateModel)
                .where(
                    PromptOptimizationCandidateModel.id == candidate_id,
                    PromptOptimizationCandidateModel.workspace_id == workspace_id,
                    PromptOptimizationCandidateModel.run_id == binding[0],
                    ~PromptOptimizationCandidateModel.deleted,
                    PromptOptimizationCandidateModel.status == expected.value,
                    live_parent,
                )
                .values(
                    status=PromptOptimizationCandidateModel.status,
                    updated_at=PromptOptimizationCandidateModel.updated_at,
                )
                .returning(PromptOptimizationCandidateModel.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if claimed is None:
            if await self.get_candidate_binding(candidate_id, workspace_id) != binding:
                raise LookupError("candidate not found")
            raise CandidateReviewError("candidate changed during review")
        return candidate, run

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
            select(PromptModel)
            .where(
                PromptModel.id == prompt_id,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
