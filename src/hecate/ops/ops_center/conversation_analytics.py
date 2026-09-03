"""Conversation analytics service.

Aggregates conversation quality metrics from ConversationModel and
ConversationTurnScoreModel. Provides overview, quality distribution,
topic distribution, low-quality conversations, and trends.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.conversation import ConversationModel
from hecate.models.conversation_turn_score import ConversationTurnScoreModel


class ConversationAnalyticsService:
    """Service for aggregating conversation analytics.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_overview(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Aggregate conversation analytics overview.

        Returns:
            Dict with total_conversations, scored_conversations,
            avg_quality_score, quality_distribution, feedback_summary.
        """
        # Total conversations
        total_q = select(func.count()).where(
            ConversationModel.created_at >= start_date,
            ConversationModel.created_at <= end_date,
            ~ConversationModel.deleted,
        )
        total = (await self._db.execute(total_q)).scalar() or 0

        # Scored conversations
        scored_q = select(func.count()).where(
            ConversationModel.created_at >= start_date,
            ConversationModel.created_at <= end_date,
            ConversationModel.quality_score.isnot(None),
            ~ConversationModel.deleted,
        )
        scored = (await self._db.execute(scored_q)).scalar() or 0

        # Average quality score
        avg_q = select(func.avg(ConversationModel.quality_score)).where(
            ConversationModel.created_at >= start_date,
            ConversationModel.created_at <= end_date,
            ConversationModel.quality_score.isnot(None),
            ~ConversationModel.deleted,
        )
        avg_score = (await self._db.execute(avg_q)).scalar()

        # Quality distribution
        dist_q = select(
            func.count().filter(ConversationModel.quality_score < 0.4).label("low"),
            func.count()
            .filter(ConversationModel.quality_score >= 0.4, ConversationModel.quality_score < 0.7)
            .label("medium"),
            func.count().filter(ConversationModel.quality_score >= 0.7).label("high"),
        ).where(
            ConversationModel.created_at >= start_date,
            ConversationModel.created_at <= end_date,
            ConversationModel.quality_score.isnot(None),
            ~ConversationModel.deleted,
        )
        dist = (await self._db.execute(dist_q)).one()

        # Feedback summary
        feedback_q = select(
            func.count().filter(ConversationTurnScoreModel.user_rating == "positive").label("positive"),
            func.count().filter(ConversationTurnScoreModel.user_rating == "negative").label("negative"),
        ).where(
            ConversationTurnScoreModel.feedback_at >= start_date,
            ConversationTurnScoreModel.feedback_at <= end_date,
            ConversationTurnScoreModel.user_rating.isnot(None),
            ~ConversationTurnScoreModel.deleted,
        )
        feedback = (await self._db.execute(feedback_q)).one()

        return {
            "total_conversations": total,
            "scored_conversations": scored,
            "avg_quality_score": round(avg_score, 4) if avg_score else None,
            "quality_distribution": {
                "low": dist.low or 0,
                "medium": dist.medium or 0,
                "high": dist.high or 0,
            },
            "feedback_summary": {
                "positive": feedback.positive or 0,
                "negative": feedback.negative or 0,
                "total": (feedback.positive or 0) + (feedback.negative or 0),
            },
        }

    async def get_quality_distribution(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Get quality score distribution histogram.

        Returns:
            List of buckets with range and count.
        """
        buckets = [
            ("0.0-0.2", 0.0, 0.2),
            ("0.2-0.4", 0.2, 0.4),
            ("0.4-0.6", 0.4, 0.6),
            ("0.6-0.8", 0.6, 0.8),
            ("0.8-1.0", 0.8, 1.0),
        ]

        result = []
        for label, low, high in buckets:
            q = select(func.count()).where(
                ConversationModel.created_at >= start_date,
                ConversationModel.created_at <= end_date,
                ConversationModel.quality_score >= low,
                ConversationModel.quality_score < high if high < 1.0 else ConversationModel.quality_score <= high,
                ~ConversationModel.deleted,
            )
            count = (await self._db.execute(q)).scalar() or 0
            result.append({"range": label, "count": count})

        return result

    async def get_topics(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Get topic distribution with conversation count and avg quality.

        Returns:
            List of topics with count and avg_quality.
        """
        q = (
            select(
                ConversationModel.topic,
                func.count().label("count"),
                func.avg(ConversationModel.quality_score).label("avg_quality"),
            )
            .where(
                ConversationModel.created_at >= start_date,
                ConversationModel.created_at <= end_date,
                ConversationModel.topic.isnot(None),
                ~ConversationModel.deleted,
            )
            .group_by(ConversationModel.topic)
            .order_by(func.count().desc())
        )
        result = (await self._db.execute(q)).all()

        return [
            {
                "topic": row.topic,
                "count": row.count,
                "avg_quality": round(row.avg_quality, 4) if row.avg_quality else None,
            }
            for row in result
        ]

    async def get_low_quality(
        self,
        threshold: float = 0.5,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get conversations with quality_score below threshold.

        Returns:
            List of conversations sorted by quality_score ascending.
        """
        q = (
            select(ConversationModel)
            .where(
                ConversationModel.quality_score < threshold,
                ConversationModel.quality_score.isnot(None),
                ~ConversationModel.deleted,
            )
            .order_by(ConversationModel.quality_score.asc())
            .limit(limit)
        )

        if start_date:
            q = q.where(ConversationModel.created_at >= start_date)
        if end_date:
            q = q.where(ConversationModel.created_at <= end_date)

        result = (await self._db.execute(q)).all()

        return [
            {
                "id": str(conv.id),
                "agent_id": str(conv.agent_id),
                "title": conv.title,
                "quality_score": conv.quality_score,
                "topic": conv.topic,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
            }
            for (conv,) in result
        ]

    async def get_conversation_turns(
        self,
        conversation_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """Get turn-level scores for a conversation.

        Returns:
            List of turn scores ordered by turn_index.
        """
        q = (
            select(ConversationTurnScoreModel)
            .where(
                ConversationTurnScoreModel.conversation_id == conversation_id,
                ~ConversationTurnScoreModel.deleted,
            )
            .order_by(ConversationTurnScoreModel.turn_index)
        )
        result = (await self._db.execute(q)).all()

        return [
            {
                "turn_index": score.turn_index,
                "message_id": str(score.message_id),
                "helpfulness": score.helpfulness,
                "coherence": score.coherence,
                "instruction_adherence": score.instruction_adherence,
                "overall_score": score.overall_score,
                "reasoning": score.reasoning,
                "topic": score.topic,
                "user_rating": score.user_rating,
                "user_comment": score.user_comment,
                "scored_at": score.scored_at.isoformat() if score.scored_at else None,
            }
            for (score,) in result
        ]

    async def get_trends(
        self,
        granularity: str = "daily",
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Get time-series of conversation count, avg quality, feedback ratio.

        Returns:
            List of data points with date, total, scored, avg_quality, feedback_ratio.
        """
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=days)

        q = select(ConversationModel).where(
            ConversationModel.created_at >= start_date,
            ConversationModel.created_at <= end_date,
            ~ConversationModel.deleted,
        )
        rows = (await self._db.execute(q)).all()

        # Bucket by granularity in Python
        buckets: dict[str, dict[str, Any]] = {}
        for (conv,) in rows:
            ts = conv.created_at
            if granularity == "hourly":
                key = ts.strftime("%Y-%m-%dT%H:00:00")
            elif granularity == "weekly":
                week_start = ts - timedelta(days=ts.weekday())
                key = week_start.strftime("%Y-%m-%d")
            else:  # daily
                key = ts.strftime("%Y-%m-%d")

            if key not in buckets:
                buckets[key] = {"date": key, "total": 0, "scored": 0, "quality_scores": []}

            buckets[key]["total"] += 1
            if conv.quality_score is not None:
                buckets[key]["scored"] += 1
                buckets[key]["quality_scores"].append(conv.quality_score)

        result = []
        for key in sorted(buckets.keys()):
            b = buckets[key]
            scores = b["quality_scores"]
            avg_quality = sum(scores) / len(scores) if scores else None
            result.append(
                {
                    "date": b["date"],
                    "total": b["total"],
                    "scored": b["scored"],
                    "avg_quality": round(avg_quality, 4) if avg_quality else None,
                }
            )

        return result
