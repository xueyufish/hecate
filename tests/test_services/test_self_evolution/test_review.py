"""Tests for candidate review, publication, unpublish, and lineage."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import EvolutionInputModel, SkillCandidateModel
from hecate.models.skill import SkillModel
from hecate.studio.self_evolution.review import (
    CandidateReviewError,
    CandidateReviewService,
)

WS_A = uuid.uuid4()
WS_B = uuid.uuid4()
RUN_ID = uuid.uuid4()


async def _seed_candidate(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID = WS_A,
    status: str = "validated",
) -> SkillCandidateModel:
    row = SkillCandidateModel(
        workspace_id=workspace_id,
        run_id=RUN_ID,
        name="verify-tool-args",
        description="Verify tool arguments before calling",
        procedure="Read the schema.",
        guardrails="Never guess formats.",
        failure_category="invalid_invocation",
        confidence=0.8,
        evidence=[{"message_index": 3, "quote": "weather_api failed"}],
        deltas=[
            {
                "content": "body",
                "source_input_id": None,
            }
        ],
        validation_report={"overall": "pass", "checks": []},
        status=status,
    )
    db.add(row)
    await db.flush()
    return row


class TestReview:
    async def test_approve_publishes_learned_skill(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session)

        service = CandidateReviewService(db_session)
        reviewed = await service.review(
            workspace_id=WS_A,
            candidate_id=candidate.id,
            reviewer="admin@example.com",
            decision="approved",
        )

        assert reviewed.status == "published"
        assert reviewed.published_skill_id is not None
        skill = await db_session.get(SkillModel, reviewed.published_skill_id)
        assert skill.source == "learned"
        assert skill.learned_run_id == RUN_ID
        assert skill.failure_category == "invalid_invocation"
        assert "## Procedure" in skill.instructions
        assert "## Guardrails" in skill.instructions

    async def test_reject_terminates_candidate(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session)

        service = CandidateReviewService(db_session)
        reviewed = await service.review(
            workspace_id=WS_A,
            candidate_id=candidate.id,
            reviewer="admin",
            decision="rejected",
            comment="too narrow",
        )

        assert reviewed.status == "rejected"
        assert reviewed.published_skill_id is None

    async def test_approve_with_edits_records_diff(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session)

        service = CandidateReviewService(db_session)
        reviewed = await service.review(
            workspace_id=WS_A,
            candidate_id=candidate.id,
            reviewer="admin",
            decision="approved_with_edits",
            edited_content={"procedure": "Read the schema carefully. Then call."},
        )

        assert reviewed.status == "published"
        assert "procedure" in (reviewed.edit_diff or {})
        skill = await db_session.get(SkillModel, reviewed.published_skill_id)
        assert "carefully" in skill.instructions

    async def test_non_validated_candidate_not_reviewable(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session, status="pending")

        service = CandidateReviewService(db_session)
        with pytest.raises(CandidateReviewError, match="not reviewable"):
            await service.review(
                workspace_id=WS_A,
                candidate_id=candidate.id,
                reviewer="admin",
                decision="approved",
            )

    async def test_content_validated_candidate_reviewable(self, db_session: AsyncSession) -> None:
        """Gate pass without behavioral eval still flows to human review."""
        candidate = await _seed_candidate(db_session, status="content_validated")

        service = CandidateReviewService(db_session)
        reviewed = await service.review(
            workspace_id=WS_A,
            candidate_id=candidate.id,
            reviewer="admin",
            decision="approved",
        )

        assert reviewed.status == "published"

    async def test_review_scoped_to_workspace(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session)

        service = CandidateReviewService(db_session)
        with pytest.raises(CandidateReviewError, match="not found"):
            await service.review(
                workspace_id=WS_B,
                candidate_id=candidate.id,
                reviewer="admin",
                decision="approved",
            )


class TestUnpublish:
    async def test_unpublish_soft_deletes_skill(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session)

        service = CandidateReviewService(db_session)
        reviewed = await service.review(
            workspace_id=WS_A,
            candidate_id=candidate.id,
            reviewer="admin",
            decision="approved",
        )
        await service.unpublish(workspace_id=WS_A, candidate_id=candidate.id)

        skill = await db_session.get(SkillModel, reviewed.published_skill_id)
        assert skill.deleted is True

    async def test_unpublish_without_skill_errors(self, db_session: AsyncSession) -> None:
        candidate = await _seed_candidate(db_session)

        service = CandidateReviewService(db_session)
        with pytest.raises(CandidateReviewError, match="no published skill"):
            await service.unpublish(workspace_id=WS_A, candidate_id=candidate.id)


class TestLineage:
    async def test_lineage_chain_assembled(self, db_session: AsyncSession) -> None:
        conversation_id = uuid.uuid4()
        source_input = EvolutionInputModel(
            workspace_id=WS_A,
            conversation_id=conversation_id,
            signal_type="quality_below_threshold",
            quality_score=0.2,
        )
        db_session.add(source_input)
        await db_session.flush()

        candidate = await _seed_candidate(db_session)
        candidate.deltas = [{"content": "body", "source_input_id": str(source_input.id)}]
        await db_session.flush()

        service = CandidateReviewService(db_session)
        chain = await service.lineage(workspace_id=WS_A, candidate_id=candidate.id)

        assert chain["failure_category"] == "invalid_invocation"
        assert chain["run_id"] == str(RUN_ID)
        assert chain["source_inputs"][0]["conversation_id"] == str(conversation_id)
        assert chain["validation_report"]["overall"] == "pass"
