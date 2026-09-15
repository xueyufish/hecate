"""Learning-input harvest for the self-evolution loop.

Watches completed conversations for two quality signals — quality scores
below a configurable threshold, and explicit user corrections — and registers
matching conversations as learning inputs for the next evolution run.

Signal checks are best-effort: a harvest failure never raises to the caller.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.conversation import ConversationModel
from hecate.models.conversation_turn_score import ConversationTurnScoreModel
from hecate.models.evolution import (
    EvolutionInputModel,
    EvolutionSignalType,
)

logger = logging.getLogger(__name__)

# Explicit-correction pre-filter signals (concept from the AgentRx-style
# failure taxonomy: the user explicitly rejects the agent's understanding).
_CORRECTION_PHRASES = (
    "that's wrong",
    "no, i meant",
    "not what i asked",
    "incorrect",
    "try again",
    "you misunderstood",
)


class LearningInputHarvester:
    """Harvests learning inputs from completed conversations."""

    def __init__(
        self,
        db: AsyncSession,
        event_store: Any | None = None,
        *,
        quality_threshold: float | None = None,
    ) -> None:
        self._db = db
        self._event_store = event_store
        if quality_threshold is None:
            from hecate.core.config import settings

            quality_threshold = settings.SKILL_EVOLUTION_QUALITY_THRESHOLD
        self._quality_threshold = quality_threshold

    async def harvest_conversation(
        self,
        conversation_id: UUID,
        workspace_id: UUID,
    ) -> EvolutionInputModel | None:
        """Evaluate one conversation's quality signals and register an input.

        Returns the created input, or ``None`` when no signal fired or the
        conversation already has a pending input (dedup).
        """
        already = await self._db.execute(
            select(EvolutionInputModel).where(
                EvolutionInputModel.conversation_id == conversation_id,
                EvolutionInputModel.status == "pending",
                ~EvolutionInputModel.deleted,
            )
        )
        if already.scalar_one_or_none() is not None:
            return None

        signal = await self._evaluate_signals(conversation_id)
        if signal is None:
            return None

        signal_type, quality_score, detail = signal
        row = EvolutionInputModel(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=await self._conversation_agent(conversation_id),
            signal_type=signal_type.value,
            quality_score=quality_score,
            detail=detail,
        )
        self._db.add(row)
        await self._db.flush()
        logger.info(
            "Harvested learning input for conversation %s (signal=%s)",
            conversation_id,
            signal_type.value,
        )
        return row

    async def harvest_recent_conversations(
        self,
        workspace_id: UUID,
        *,
        lookback_hours: int = 24,
        limit: int = 100,
    ) -> list[EvolutionInputModel]:
        """Sweep recently-scored conversations of a workspace for signals.

        This is the batch collection entry point invoked by the evolution
        meta-agent; per-conversation quality scores may land asynchronously
        after the chat response, so completion is observed with sweep
        latency rather than inline.
        """
        since = datetime.now(UTC) - timedelta(hours=lookback_hours)
        result = await self._db.execute(
            select(ConversationModel.id).where(
                ConversationModel.workspace_id == workspace_id,
                ConversationModel.quality_scored_at.isnot(None),
                ConversationModel.quality_scored_at >= since,
                ~ConversationModel.deleted,
            )
        )
        conversation_ids = [row for (row,) in result.all()]

        harvested: list[EvolutionInputModel] = []
        for conversation_id in conversation_ids[:limit]:
            try:
                row = await self.harvest_conversation(conversation_id, workspace_id)
            except Exception:
                logger.warning("Harvest failed for conversation %s", conversation_id, exc_info=True)
                continue
            if row is not None:
                harvested.append(row)
        return harvested

    async def _evaluate_signals(self, conversation_id: UUID) -> tuple[EvolutionSignalType, float | None, dict] | None:
        """Return the first firing signal for a conversation, if any."""
        avg_adherence = await self._avg_instruction_adherence(conversation_id)
        quality_score = (
            avg_adherence if avg_adherence is not None else await self._conversation_quality_score(conversation_id)
        )

        if quality_score is not None and quality_score < self._quality_threshold:
            return (
                EvolutionSignalType.QUALITY_BELOW_THRESHOLD,
                quality_score,
                {
                    "threshold": self._quality_threshold,
                    "source": "turn_scores" if avg_adherence is not None else "conversation",
                },
            )

        correction = await self._has_user_correction(conversation_id)
        if correction:
            return (
                EvolutionSignalType.USER_CORRECTION,
                quality_score,
                {"phrases": list(_CORRECTION_PHRASES)},
            )

        return None

    async def _avg_instruction_adherence(self, conversation_id: UUID) -> float | None:
        """Average instruction_adherence over scored turns, if any exist."""
        result = await self._db.execute(
            select(func.avg(ConversationTurnScoreModel.instruction_adherence)).where(
                ConversationTurnScoreModel.conversation_id == conversation_id,
                ConversationTurnScoreModel.instruction_adherence.isnot(None),
                ~ConversationTurnScoreModel.deleted,
            )
        )
        avg = result.scalar_one_or_none()
        return float(avg) if avg is not None else None

    async def _conversation_quality_score(self, conversation_id: UUID) -> float | None:
        """Conversation-level aggregate quality score, if scored."""
        result = await self._db.execute(
            select(ConversationModel.quality_score).where(
                ConversationModel.id == conversation_id,
                ~ConversationModel.deleted,
            )
        )
        score = result.scalar_one_or_none()
        return float(score) if score is not None else None

    async def _has_user_correction(self, conversation_id: UUID) -> bool:
        """Detect explicit user correction in the conversation messages."""
        from hecate.ops.ops_center.conversation_messages import (
            project_conversation_messages,
        )

        try:
            messages = await project_conversation_messages(self._db, conversation_id, self._event_store)
        except Exception:
            logger.warning(
                "Message projection failed for conversation %s",
                conversation_id,
                exc_info=True,
            )
            return False

        for message in messages:
            if message.get("role") != "user":
                continue
            content = str(message.get("content", "")).lower()
            if any(phrase in content for phrase in _CORRECTION_PHRASES):
                return True
        return False

    async def _conversation_agent(self, conversation_id: UUID) -> UUID | None:
        """Resolve the conversation's agent ID."""
        result = await self._db.execute(
            select(ConversationModel.agent_id).where(
                ConversationModel.id == conversation_id,
                ~ConversationModel.deleted,
            )
        )
        return result.scalar_one_or_none()
