"""Human review and publication of candidate skills.

The promotion gate of the self-evolution loop: validated candidates become
published ``SkillModel`` rows (``source="learned"`` with provenance) only
through an explicit reviewer decision; published skills can be unpublished
(soft delete) with lineage retained.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionInputModel,
    SkillCandidateModel,
    SkillCandidateStatus,
)
from hecate.models.skill import SkillModel
from hecate.studio.self_evolution.gate import candidate_content_hash
from hecate.studio.self_evolution.repository import EvolutionRepository

logger = logging.getLogger(__name__)

_EDITABLE_FIELDS = ("name", "description", "procedure", "guardrails")


class CandidateReviewError(Exception):
    """Review operation cannot be applied to the candidate."""


def _compose_instructions(candidate: SkillCandidateModel) -> str:
    """Render candidate partitions as SKILL.md-style instructions."""
    return f"## Procedure\n\n{candidate.procedure}\n\n## Guardrails\n\n{candidate.guardrails}"


class CandidateReviewService:
    """Applies reviewer decisions and manages publication."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = EvolutionRepository(db)

    async def review(
        self,
        *,
        workspace_id: UUID,
        candidate_id: UUID,
        reviewer: str,
        decision: str,
        comment: str | None = None,
        edited_content: dict | None = None,
    ) -> SkillCandidateModel:
        """Apply a reviewer decision: approve / approve_with_edits / reject.

        Only gate-passed candidates are reviewable — ``validated`` (all four
        checks ran and passed) or ``content_validated`` (content checks
        passed, behavioral evaluation was skipped; the human reviewer is the
        remaining evidence). Approval publishes a learned skill with full
        provenance.
        """
        candidate = await self._repo.get_candidate(workspace_id, candidate_id)
        if candidate is None:
            raise CandidateReviewError(f"Candidate {candidate_id} not found")
        reviewable = (
            SkillCandidateStatus.VALIDATED.value,
            SkillCandidateStatus.CONTENT_VALIDATED.value,
        )
        if candidate.status not in reviewable:
            raise CandidateReviewError(f"Candidate {candidate_id} is not reviewable (status={candidate.status})")

        if decision == "rejected":
            candidate.status = SkillCandidateStatus.REJECTED.value
            candidate.review_decision = decision
            candidate.reviewed_by = reviewer
            candidate.reviewed_at = datetime.now(UTC)
            candidate.review_comment = comment
            await self._db.flush()
            return candidate

        if decision not in ("approved", "approved_with_edits"):
            raise CandidateReviewError(f"Unknown review decision: {decision}")

        edit_diff: dict = {}
        if edited_content:
            for field_name in _EDITABLE_FIELDS:
                if field_name in edited_content:
                    before = getattr(candidate, field_name)
                    after = str(edited_content[field_name])
                    if before != after:
                        edit_diff[field_name] = {"before": before, "after": after}
                        setattr(candidate, field_name, after)

        # Review-revalidation (B3): the gate bound its verdict to the exact
        # content it validated (validation_report.content_hash). If the
        # content at review time differs — edited here or drifted through
        # any other path — mark the report stale before publishing so the
        # published skill carries the honest provenance: human-approved,
        # but not the content the behavioral checks ran against.
        report = dict(candidate.validation_report or {})
        recorded_hash = report.get("content_hash")
        if recorded_hash and recorded_hash != candidate_content_hash(candidate) and not report.get("stale"):
            report["stale"] = True
            report["stale_reason"] = "content_changed_after_validation"
            candidate.validation_report = report

        skill = await self._publish_skill(workspace_id, candidate)

        candidate.status = SkillCandidateStatus.PUBLISHED.value
        candidate.review_decision = decision
        candidate.reviewed_by = reviewer
        candidate.reviewed_at = datetime.now(UTC)
        candidate.review_comment = comment
        candidate.edit_diff = edit_diff or None
        candidate.published_skill_id = skill.id
        await self._db.flush()

        logger.info(
            "Candidate %s %s by %s (skill %s)",
            candidate_id,
            decision,
            reviewer,
            skill.name,
        )
        return candidate

    async def unpublish(
        self,
        *,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> SkillCandidateModel:
        """Withdraw a published learned skill (soft delete; lineage stays)."""
        candidate = await self._repo.get_candidate(workspace_id, candidate_id)
        if candidate is None:
            raise CandidateReviewError(f"Candidate {candidate_id} not found")
        if candidate.published_skill_id is None:
            raise CandidateReviewError(f"Candidate {candidate_id} has no published skill")

        result = await self._db.get(SkillModel, candidate.published_skill_id)
        if result is not None:
            result.deleted = True
            result.deleted_at = datetime.now(UTC)
        await self._db.flush()
        logger.info("Unpublished skill for candidate %s", candidate_id)
        return candidate

    async def lineage(
        self,
        *,
        workspace_id: UUID,
        candidate_id: UUID,
    ) -> dict:
        """Full provenance chain: trajectory → attribution → gate → review."""
        candidate = await self._repo.get_candidate(workspace_id, candidate_id)
        if candidate is None:
            raise CandidateReviewError(f"Candidate {candidate_id} not found")

        source_input_ids = {
            delta.get("source_input_id")
            for delta in (candidate.deltas or [])
            if isinstance(delta, dict) and delta.get("source_input_id")
        }
        inputs: list[EvolutionInputModel] = []
        for raw_id in source_input_ids:
            try:
                input_id = UUID(str(raw_id))
            except ValueError:
                continue
            row = await self._repo.get_input(workspace_id, input_id)
            if row is not None:
                inputs.append(row)

        return {
            "candidate_id": str(candidate.id),
            "run_id": str(candidate.run_id) if candidate.run_id else None,
            "failure_category": candidate.failure_category,
            "source_inputs": [
                {
                    "input_id": str(row.id),
                    "conversation_id": str(row.conversation_id) if row.conversation_id else None,
                    "signal_type": row.signal_type,
                    "quality_score": row.quality_score,
                    "detail": row.detail,
                }
                for row in inputs
            ],
            "deltas": candidate.deltas,
            "validation_report": candidate.validation_report,
            "review": {
                "decision": candidate.review_decision,
                "reviewed_by": candidate.reviewed_by,
                "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
                "comment": candidate.review_comment,
                "edit_diff": candidate.edit_diff,
            },
            "published_skill_id": str(candidate.published_skill_id) if candidate.published_skill_id else None,
        }

    async def _publish_skill(self, workspace_id: UUID, candidate: SkillCandidateModel) -> SkillModel:
        """Create (or refresh) the published SkillModel for a candidate."""
        instructions = _compose_instructions(candidate)
        if candidate.published_skill_id is not None:
            existing = await self._db.get(SkillModel, candidate.published_skill_id)
            if existing is not None and not existing.deleted:
                existing.description = candidate.description
                existing.instructions = instructions
                await self._flush_skill_content_hash(existing)
                await self._auto_commit_version(existing, candidate)
                return existing

        skill = SkillModel(
            workspace_id=workspace_id,
            name=candidate.name,
            description=candidate.description,
            source="learned",
            instructions=instructions,
            learned_run_id=candidate.run_id,
            failure_category=candidate.failure_category,
            max_tokens=2000,
            auto_load=False,
        )
        self._db.add(skill)
        await self._db.flush()
        await self._auto_commit_version(skill, candidate)
        return skill

    async def _flush_skill_content_hash(self, skill: SkillModel) -> None:
        """Refresh the live content hash after a self-evolution write.

        Mirrors the workspace-skill update path so drift detection (5.9d)
        sees a stable comparison basis.
        """
        from hecate.tools.api.skills import _compute_content_hash

        skill.content_hash = _compute_content_hash(skill)
        await self._db.flush()

    async def _auto_commit_version(self, skill: SkillModel, candidate: SkillCandidateModel) -> None:
        """Snapshot a self-evolution publish for audit (5.9d).

        Best-effort: a versioning failure never blocks the publish — the
        audit gap remains visible through the existing publish event and
        warning log. ``learned_run_id`` ties the snapshot to the run that
        produced it; ``created_by`` stays ``None`` (system-created) per
        the service contract.
        """
        from hecate.tools.skill.versioning import (
            SkillNotVersionableError,
            SkillVersionService,
        )

        try:
            await SkillVersionService(self._db).commit(
                skill.id,
                change_summary=f"Self-evolution publish from run {candidate.run_id}",
                learned_run_id=candidate.run_id,
            )
        except SkillNotVersionableError:
            # Learned skills start source="learned" with provider unset
            # until the migration that promotes them; skip silently and
            # let future publishes pick up versioning once the provider
            # is assigned. Logged so the audit gap is visible.
            logger.warning(
                "Skill %s (id=%s) has no provider assigned; skipping auto-versioning",
                skill.name,
                skill.id,
            )
        except Exception:
            logger.exception(
                "Auto-versioning failed for skill %s (id=%s); publish continues",
                skill.name,
                skill.id,
            )
