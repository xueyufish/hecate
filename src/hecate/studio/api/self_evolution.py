"""Self-evolution loop API endpoints.

Studio-side visibility into the evolution pipeline (1.3.6f):

- ``GET  /api/self-evolution/inputs`` — list learning inputs
- ``POST /api/self-evolution/inputs/harvest`` — trigger a harvest sweep
- ``GET  /api/self-evolution/runs`` — list evolution runs
- ``GET  /api/self-evolution/candidates`` — list candidate skills
- ``GET  /api/self-evolution/candidates/{id}`` — candidate detail
- ``POST /api/self-evolution/candidates/{id}/review`` — reviewer decision
- ``POST /api/self-evolution/candidates/{id}/unpublish`` — withdraw a skill
- ``GET  /api/self-evolution/candidates/{id}/lineage`` — provenance chain

All reads are workspace-scoped to the caller's auth context.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evolution import SkillCandidateReviewSchema
from hecate.studio.self_evolution.harvest import LearningInputHarvester
from hecate.studio.self_evolution.repository import EvolutionRepository
from hecate.studio.self_evolution.review import CandidateReviewError, CandidateReviewService

router = APIRouter()


def _dump_rows(rows: list[Any]) -> list[dict]:
    """Serialize ORM rows to dicts via their column keys."""
    return [{c.key: getattr(row, c.key) for c in row.__table__.columns} for row in rows]


@router.get("/self-evolution/inputs")
async def list_learning_inputs(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """List learning inputs of the caller's workspace."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    repo = EvolutionRepository(db)
    rows = await repo.list_inputs(workspace_id, limit=limit)
    if status is not None:
        rows = [row for row in rows if row.status == status]
    return {"items": _dump_rows(rows), "total": len(rows)}


@router.post("/self-evolution/inputs/harvest")
async def trigger_harvest(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    lookback_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> dict:
    """Run a harvest sweep over recently scored conversations."""
    from hecate.core.config import settings

    if not settings.SKILL_EVOLUTION_ENABLED:
        return {"harvested": 0, "enabled": False}

    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    harvester = LearningInputHarvester(db)
    rows = await harvester.harvest_recent_conversations(workspace_id, lookback_hours=lookback_hours)
    return {"harvested": len(rows), "enabled": True}


@router.get("/self-evolution/runs")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """List evolution runs of the caller's workspace."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    repo = EvolutionRepository(db)
    rows = await repo.list_runs(workspace_id, limit=limit)
    return {"items": _dump_rows(rows), "total": len(rows)}


@router.get("/self-evolution/candidates")
async def list_candidates(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    """List candidate skills of the caller's workspace."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    repo = EvolutionRepository(db)
    rows = await repo.list_candidates(workspace_id, status=status, limit=limit)
    return {"items": _dump_rows(rows), "total": len(rows)}


@router.get("/self-evolution/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Candidate detail including validation report and deltas."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    repo = EvolutionRepository(db)
    row = await repo.get_candidate(workspace_id, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return _dump_rows([row])[0]


@router.post("/self-evolution/candidates/{candidate_id}/review")
async def review_candidate(
    candidate_id: uuid.UUID,
    data: SkillCandidateReviewSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Apply a reviewer decision to a validated candidate."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = CandidateReviewService(db)
    try:
        row = await service.review(
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            reviewer=data.reviewer,
            decision=data.decision,
            comment=data.comment,
            edited_content=data.edited_content,
        )
    except CandidateReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.refresh(row)
    return _dump_rows([row])[0]


@router.post("/self-evolution/candidates/{candidate_id}/unpublish")
async def unpublish_candidate(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Withdraw a published learned skill."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = CandidateReviewService(db)
    try:
        row = await service.unpublish(workspace_id=workspace_id, candidate_id=candidate_id)
    except CandidateReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.refresh(row)
    return _dump_rows([row])[0]


@router.get("/self-evolution/candidates/{candidate_id}/lineage")
async def get_candidate_lineage(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Full provenance chain of a candidate."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = CandidateReviewService(db)
    try:
        return await service.lineage(workspace_id=workspace_id, candidate_id=candidate_id)
    except CandidateReviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/self-evolution/skills/{skill_name}/usage")
async def get_skill_usage(
    skill_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Usage statistics for a learned skill: counts and quality comparison.

    The comparison contrasts conversations where the skill was loaded
    (L2) against the agent's other scored conversations. When no usage
    events carry session linkage, the comparison is reported as
    unavailable rather than fabricated.
    """
    from sqlalchemy import func, select

    from hecate.models.conversation import ConversationModel
    from hecate.models.evolution import SkillUsageEventModel
    from hecate.models.session import SessionModel

    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    events = (
        (
            await db.execute(
                select(SkillUsageEventModel).where(
                    SkillUsageEventModel.workspace_id == workspace_id,
                    SkillUsageEventModel.skill_name == skill_name,
                    ~SkillUsageEventModel.deleted,
                )
            )
        )
        .scalars()
        .all()
    )

    catalog_served = sum(1 for e in events if e.event_type == "catalog_served")
    loaded = [e for e in events if e.event_type == "skill_loaded"]
    last_loaded = max((e.created_at for e in loaded), default=None)

    quality_comparison: dict | None = None
    used_conversation_ids: set[uuid.UUID] = set()
    for event in loaded:
        if event.session_id is None:
            continue
        conv_id = (
            await db.execute(select(SessionModel.conversation_id).where(SessionModel.id == event.session_id))
        ).scalar_one_or_none()
        if conv_id is not None:
            used_conversation_ids.add(conv_id)

    if used_conversation_ids:
        used_scores = (
            await db.execute(
                select(func.avg(ConversationModel.quality_score)).where(
                    ConversationModel.id.in_(used_conversation_ids),
                    ConversationModel.quality_score.isnot(None),
                )
            )
        ).scalar_one_or_none()
        agent_ids = [e.agent_id for e in loaded if e.agent_id is not None]
        unused_scores = None
        if agent_ids:
            unused_scores = (
                await db.execute(
                    select(func.avg(ConversationModel.quality_score)).where(
                        ConversationModel.agent_id.in_(agent_ids),
                        ConversationModel.workspace_id == workspace_id,
                        ConversationModel.quality_score.isnot(None),
                        ~ConversationModel.id.in_(used_conversation_ids) if used_conversation_ids else True,
                    )
                )
            ).scalar_one_or_none()
        quality_comparison = {
            "used_conversations": len(used_conversation_ids),
            "used_avg_quality": round(float(used_scores), 3) if used_scores is not None else None,
            "unused_avg_quality": round(float(unused_scores), 3) if unused_scores is not None else None,
        }

    return {
        "skill_name": skill_name,
        "catalog_served": catalog_served,
        "l2_loads": len(loaded),
        "last_loaded_at": last_loaded.isoformat() if last_loaded else None,
        "quality_comparison": quality_comparison,
    }
