"""Conversation quality scorer service.

Evaluates conversation turns using LLM-as-Judge. Scores each assistant turn
on helpfulness, coherence, and instruction_adherence. Classifies conversation
topic. Stores results in ConversationTurnScoreModel.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.conversation import ConversationModel
from hecate.models.conversation_turn_score import ConversationTurnScoreModel
from hecate.models.message import MessageModel
from hecate.services.llm.service import llm_service

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "You are a conversation quality evaluator. Your task is to evaluate the quality "
    "of an assistant's response in the context of a multi-turn conversation.\n\n"
    "You will receive:\n"
    "1. The full conversation history (all prior turns)\n"
    "2. The specific assistant turn to evaluate\n\n"
    "You must evaluate the assistant's response on three dimensions:\n"
    "- **helpfulness** (0.0-1.0): Does the response address the user's need?\n"
    "- **coherence** (0.0-1.0): Is the response logically consistent?\n"
    "- **instruction_adherence** (0.0-1.0): Does the response follow system prompt constraints?\n\n"
    "You must also classify the conversation topic into one of these categories:\n"
    "- billing, technical_support, feature_request, general_inquiry, feedback, other\n\n"
    "Respond with ONLY a JSON object (no markdown, no code fences):\n"
    '{{"helpfulness": <float>, "coherence": <float>, "instruction_adherence": <float>, '
    '"topic": "<category>", "reasoning": "<brief explanation>"}}'
)


def _build_judge_prompt(
    messages: list[dict[str, Any]],
    turn_index: int,
) -> list[dict[str, Any]]:
    """Build LLM prompt for quality evaluation.

    Args:
        messages: All conversation messages (user + assistant).
        turn_index: Index of the assistant turn to evaluate.

    Returns:
        List of messages for the LLM judge.
    """
    # Build conversation context (all turns up to and including the target)
    conversation_text = []
    for _i, msg in enumerate(messages[: turn_index + 1]):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        prefix = "Assistant" if role == "assistant" else "User" if role == "user" else role.capitalize()
        conversation_text.append(f"{prefix}: {content}")

    context = "\n\n".join(conversation_text)

    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Evaluate the assistant's response in turn {turn_index + 1}.\n\nConversation:\n{context}",
        },
    ]


def _parse_judge_response(response_content: str) -> dict[str, Any] | None:
    """Parse LLM judge response into structured scores.

    Args:
        response_content: Raw LLM response content.

    Returns:
        Dict with helpfulness, coherence, instruction_adherence, topic, reasoning.
        None if parsing fails.
    """
    try:
        # Try direct JSON parse
        data = json.loads(response_content)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code fences
        import re

        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response_content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                logger.warning("Failed to parse judge response as JSON: %s", response_content[:200])
                return None
        else:
            logger.warning("Failed to parse judge response as JSON: %s", response_content[:200])
            return None

    # Validate required fields
    required = ["helpfulness", "coherence", "instruction_adherence", "topic", "reasoning"]
    if not all(k in data for k in required):
        logger.warning("Judge response missing required fields: %s", list(data.keys()))
        return None

    # Clamp scores to [0.0, 1.0]
    for field in ["helpfulness", "coherence", "instruction_adherence"]:
        data[field] = max(0.0, min(1.0, float(data[field])))

    # Compute overall score (weighted average)
    weights = {"helpfulness": 0.4, "coherence": 0.3, "instruction_adherence": 0.3}
    data["overall_score"] = sum(data[k] * weights[k] for k in weights)

    return data


class ConversationQualityScorer:
    """Service for evaluating conversation quality using LLM-as-Judge.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def score_turn(
        self,
        conversation_id: uuid.UUID,
        messages: list[dict[str, Any]],
        turn_index: int,
        message_id: uuid.UUID,
    ) -> ConversationTurnScoreModel | None:
        """Score a single assistant turn using LLM-as-Judge.

        Args:
            conversation_id: The conversation UUID.
            messages: All conversation messages.
            turn_index: Index of the assistant turn to score.
            message_id: UUID of the assistant message.

        Returns:
            ConversationTurnScoreModel record, or None if scoring fails.
        """
        judge_model = settings.CONVERSATION_QUALITY_JUDGE_MODEL
        prompt = _build_judge_prompt(messages, turn_index)

        try:
            response = await llm_service.chat(
                messages=prompt,
                model=judge_model,
                temperature=0.0,
                max_tokens=500,
                timeout=30.0,
            )
        except Exception as e:
            logger.warning("LLM judge call failed for conversation %s turn %d: %s", conversation_id, turn_index, e)
            return None

        if not response.content:
            logger.warning("LLM judge returned empty response for conversation %s turn %d", conversation_id, turn_index)
            return None

        scores = _parse_judge_response(response.content)
        if not scores:
            return None

        record = ConversationTurnScoreModel(
            conversation_id=conversation_id,
            message_id=message_id,
            turn_index=turn_index,
            helpfulness=scores["helpfulness"],
            coherence=scores["coherence"],
            instruction_adherence=scores["instruction_adherence"],
            overall_score=scores["overall_score"],
            reasoning=scores["reasoning"],
            topic=scores["topic"],
            scored_at=datetime.now(UTC),
        )
        self._db.add(record)
        await self._db.flush()

        logger.info(
            "Scored conversation %s turn %d: overall=%.2f topic=%s",
            conversation_id,
            turn_index,
            scores["overall_score"],
            scores["topic"],
        )
        return record

    async def score_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> list[ConversationTurnScoreModel]:
        """Score all assistant turns in a conversation.

        Args:
            conversation_id: The conversation UUID.

        Returns:
            List of ConversationTurnScoreModel records created.
        """
        # Load messages
        msg_q = (
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role.in_(["user", "assistant"]),
                ~MessageModel.deleted,
            )
            .order_by(MessageModel.created_at)
        )
        msg_result = (await self._db.execute(msg_q)).all()
        messages = [{"role": m.role, "content": m.content, "id": str(m.id)} for (m,) in msg_result]

        if not messages:
            logger.warning("No messages found for conversation %s", conversation_id)
            return []

        # Find assistant turns and score them
        scored_records = []
        for i, msg in enumerate(messages):
            if msg["role"] == "assistant":
                # Find the message_id from the original MessageModel
                original_msg = msg_result[i][0]
                record = await self.score_turn(
                    conversation_id=conversation_id,
                    messages=messages,
                    turn_index=i,
                    message_id=original_msg.id,
                )
                if record:
                    scored_records.append(record)

        # Aggregate to conversation level
        if scored_records:
            await self._aggregate_to_conversation(conversation_id, scored_records)

        return scored_records

    async def _aggregate_to_conversation(
        self,
        conversation_id: uuid.UUID,
        turn_scores: list[ConversationTurnScoreModel],
    ) -> None:
        """Compute conversation-level aggregate scores from turn scores.

        Args:
            conversation_id: The conversation UUID.
            turn_scores: List of scored turn records.
        """
        conv_q = select(ConversationModel).where(ConversationModel.id == conversation_id)
        conv = (await self._db.execute(conv_q)).scalar_one_or_none()
        if not conv:
            logger.warning("Conversation %s not found for aggregation", conversation_id)
            return

        overall_scores = [s.overall_score for s in turn_scores if s.overall_score is not None]
        if not overall_scores:
            return

        # Aggregate scores
        conv.quality_score = sum(overall_scores) / len(overall_scores)
        conv.quality_min_score = min(overall_scores)
        conv.quality_scored_at = datetime.now(UTC)

        # Aggregate dimension scores
        helpfulness_scores = [s.helpfulness for s in turn_scores if s.helpfulness is not None]
        coherence_scores = [s.coherence for s in turn_scores if s.coherence is not None]
        adherence_scores = [s.instruction_adherence for s in turn_scores if s.instruction_adherence is not None]

        conv.quality_metrics = {
            "helpfulness": sum(helpfulness_scores) / len(helpfulness_scores) if helpfulness_scores else None,
            "coherence": sum(coherence_scores) / len(coherence_scores) if coherence_scores else None,
            "instruction_adherence": sum(adherence_scores) / len(adherence_scores) if adherence_scores else None,
            "turn_count": len(turn_scores),
        }

        # Use topic from the last scored turn
        topics = [s.topic for s in turn_scores if s.topic]
        if topics:
            conv.topic = topics[-1]

        await self._db.flush()

        logger.info(
            "Aggregated conversation %s: quality=%.2f min=%.2f topic=%s",
            conversation_id,
            conv.quality_score,
            conv.quality_min_score,
            conv.topic,
        )
