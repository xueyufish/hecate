"""Conversation analytics REST API — quality metrics, topics, feedback."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.models.conversation import ConversationModel
from hecate.models.conversation_turn_score import ConversationTurnScoreModel, FeedbackSubmitSchema
from hecate.services.ops_center.conversation_analytics import ConversationAnalyticsService

router = APIRouter(prefix="/api/ops-center/conversations", tags=["ops-center"])


@router.get("/overview")
async def get_overview(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Aggregate conversation analytics overview."""
    service = ConversationAnalyticsService(db)
    return await service.get_overview(start_date, end_date)


@router.get("/quality-distribution")
async def get_quality_distribution(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Quality score distribution histogram."""
    service = ConversationAnalyticsService(db)
    return await service.get_quality_distribution(start_date, end_date)


@router.get("/topics")
async def get_topics(
    start_date: datetime = Query(...),  # noqa: B008
    end_date: datetime = Query(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Topic distribution with conversation count and avg quality."""
    service = ConversationAnalyticsService(db)
    return await service.get_topics(start_date, end_date)


@router.get("/low-quality")
async def get_low_quality(
    threshold: float = Query(0.5, ge=0.0, le=1.0),  # noqa: B008
    start_date: datetime | None = Query(None),  # noqa: B008
    end_date: datetime | None = Query(None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Conversations with quality_score below threshold."""
    service = ConversationAnalyticsService(db)
    return await service.get_low_quality(threshold, start_date, end_date)


@router.get("/{conversation_id}/turns")
async def get_conversation_turns(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Turn-level scores for a conversation."""
    service = ConversationAnalyticsService(db)
    return await service.get_conversation_turns(conversation_id)


@router.post("/{conversation_id}/turns/{turn_index}/feedback")
async def submit_feedback(
    conversation_id: uuid.UUID,
    turn_index: int,
    feedback: FeedbackSubmitSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Submit user feedback for a turn."""
    # Find the turn score record
    q = select(ConversationTurnScoreModel).where(
        ConversationTurnScoreModel.conversation_id == conversation_id,
        ConversationTurnScoreModel.turn_index == turn_index,
        ~ConversationTurnScoreModel.deleted,
    )
    record = (await db.execute(q)).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Turn score not found")

    # Update feedback
    from datetime import UTC

    record.user_rating = feedback.rating
    record.user_comment = feedback.comment
    record.feedback_at = datetime.now(UTC)
    await db.flush()

    # Update conversation feedback summary
    summary_q = select(
        ConversationTurnScoreModel.user_rating,
    ).where(
        ConversationTurnScoreModel.conversation_id == conversation_id,
        ConversationTurnScoreModel.user_rating.isnot(None),
        ~ConversationTurnScoreModel.deleted,
    )
    ratings = (await db.execute(summary_q)).all()

    positive = sum(1 for (r,) in ratings if r == "positive")
    negative = sum(1 for (r,) in ratings if r == "negative")

    conv_q = select(ConversationModel).where(ConversationModel.id == conversation_id)
    conv = (await db.execute(conv_q)).scalar_one_or_none()
    if conv:
        conv.feedback_summary = {"positive": positive, "negative": negative, "total": positive + negative}
        await db.flush()

    return {
        "turn_index": turn_index,
        "user_rating": feedback.rating,
        "user_comment": feedback.comment,
        "feedback_summary": conv.feedback_summary if conv else None,
    }


@router.get("/trends")
async def get_trends(
    granularity: str = Query("daily", pattern="^(hourly|daily|weekly)$"),  # noqa: B008
    days: int = Query(7, ge=1, le=90),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Time-series of conversation count, avg quality, feedback ratio."""
    service = ConversationAnalyticsService(db)
    return await service.get_trends(granularity, days)
