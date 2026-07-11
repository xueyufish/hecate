"""Tests for ConversationQualityScorer — turn-level LLM-as-Judge scoring."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.conversation import ConversationModel
from hecate.models.message import MessageModel
from hecate.services.ops_center.conversation_quality_scorer import (
    ConversationQualityScorer,
    _build_judge_prompt,
    _parse_judge_response,
)


async def _create_conversation(db: AsyncSession, agent_id: uuid.UUID | None = None) -> ConversationModel:
    """Helper to create a conversation."""
    conv = ConversationModel(
        agent_id=agent_id or uuid.uuid4(),
        title="Test Conversation",
    )
    db.add(conv)
    await db.flush()
    return conv


async def _create_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    role: str = "user",
    content: str = "Hello",
) -> MessageModel:
    """Helper to create a message."""
    msg = MessageModel(
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(msg)
    await db.flush()
    return msg


class TestBuildJudgePrompt:
    """Tests for _build_judge_prompt()."""

    def test_single_turn(self) -> None:
        """Prompt includes conversation context for single turn."""
        messages = [
            {"role": "user", "content": "How do I reset my password?"},
            {"role": "assistant", "content": "Go to settings and click reset."},
        ]
        prompt = _build_judge_prompt(messages, turn_index=1)
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert prompt[1]["role"] == "user"
        assert "turn 2" in prompt[1]["content"]
        assert "How do I reset my password?" in prompt[1]["content"]
        assert "Go to settings and click reset." in prompt[1]["content"]

    def test_multi_turn_context(self) -> None:
        """Prompt includes all prior turns for multi-turn context."""
        messages = [
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"},
            {"role": "assistant", "content": "Answer 2"},
        ]
        prompt = _build_judge_prompt(messages, turn_index=3)
        assert "Question 1" in prompt[1]["content"]
        assert "Answer 1" in prompt[1]["content"]
        assert "Question 2" in prompt[1]["content"]
        assert "Answer 2" in prompt[1]["content"]


class TestParseJudgeResponse:
    """Tests for _parse_judge_response()."""

    def test_valid_json(self) -> None:
        """Parses valid JSON response correctly."""
        response = json.dumps(
            {
                "helpfulness": 0.9,
                "coherence": 0.85,
                "instruction_adherence": 0.8,
                "topic": "technical_support",
                "reasoning": "Clear and helpful response.",
            }
        )
        result = _parse_judge_response(response)
        assert result is not None
        assert result["helpfulness"] == 0.9
        assert result["coherence"] == 0.85
        assert result["instruction_adherence"] == 0.8
        assert result["topic"] == "technical_support"
        assert "overall_score" in result

    def test_json_in_code_fence(self) -> None:
        """Parses JSON wrapped in markdown code fences."""
        response = (
            '```json\n{"helpfulness": 0.7, "coherence": 0.6, '
            '"instruction_adherence": 0.5, "topic": "billing", "reasoning": "ok"}\n```'
        )
        result = _parse_judge_response(response)
        assert result is not None
        assert result["helpfulness"] == 0.7

    def test_invalid_json(self) -> None:
        """Returns None for invalid JSON."""
        assert _parse_judge_response("not json") is None

    def test_missing_fields(self) -> None:
        """Returns None when required fields are missing."""
        response = json.dumps({"helpfulness": 0.9})
        assert _parse_judge_response(response) is None

    def test_score_clamping(self) -> None:
        """Clamps scores to [0.0, 1.0]."""
        response = json.dumps(
            {
                "helpfulness": 1.5,
                "coherence": -0.1,
                "instruction_adherence": 0.5,
                "topic": "general",
                "reasoning": "test",
            }
        )
        result = _parse_judge_response(response)
        assert result is not None
        assert result["helpfulness"] == 1.0
        assert result["coherence"] == 0.0


class TestScoreTurn:
    """Tests for ConversationQualityScorer.score_turn()."""

    async def test_score_single_turn(self, db_session: AsyncSession) -> None:
        """Scores a single assistant turn and creates record."""
        conv = await _create_conversation(db_session)
        await _create_message(db_session, conv.id, "user", "How do I reset?")
        asst_msg = await _create_message(db_session, conv.id, "assistant", "Go to settings.")

        mock_response = AsyncMock()
        mock_response.content = json.dumps(
            {
                "helpfulness": 0.9,
                "coherence": 0.85,
                "instruction_adherence": 0.8,
                "topic": "technical_support",
                "reasoning": "Clear response.",
            }
        )

        with patch(
            "hecate.services.ops_center.conversation_quality_scorer.llm_service.chat", return_value=mock_response
        ):
            scorer = ConversationQualityScorer(db_session)
            result = await scorer.score_turn(
                conversation_id=conv.id,
                messages=[
                    {"role": "user", "content": "How do I reset?"},
                    {"role": "assistant", "content": "Go to settings."},
                ],
                turn_index=1,
                message_id=asst_msg.id,
            )

        assert result is not None
        assert result.helpfulness == 0.9
        assert result.coherence == 0.85
        assert result.instruction_adherence == 0.8
        assert result.topic == "technical_support"
        assert result.overall_score is not None

    async def test_llm_failure_returns_none(self, db_session: AsyncSession) -> None:
        """Returns None when LLM call fails."""
        conv = await _create_conversation(db_session)
        asst_msg = await _create_message(db_session, conv.id, "assistant", "Hello")

        with patch(
            "hecate.services.ops_center.conversation_quality_scorer.llm_service.chat",
            side_effect=Exception("LLM error"),
        ):
            scorer = ConversationQualityScorer(db_session)
            result = await scorer.score_turn(
                conversation_id=conv.id,
                messages=[{"role": "assistant", "content": "Hello"}],
                turn_index=0,
                message_id=asst_msg.id,
            )

        assert result is None


class TestScoreConversation:
    """Tests for ConversationQualityScorer.score_conversation()."""

    async def test_scores_all_assistant_turns(self, db_session: AsyncSession) -> None:
        """Scores all assistant turns in a conversation."""
        conv = await _create_conversation(db_session)
        await _create_message(db_session, conv.id, "user", "Q1")
        await _create_message(db_session, conv.id, "assistant", "A1")
        await _create_message(db_session, conv.id, "user", "Q2")
        await _create_message(db_session, conv.id, "assistant", "A2")

        mock_response = AsyncMock()
        mock_response.content = json.dumps(
            {
                "helpfulness": 0.8,
                "coherence": 0.8,
                "instruction_adherence": 0.8,
                "topic": "general_inquiry",
                "reasoning": "Good response.",
            }
        )

        with patch(
            "hecate.services.ops_center.conversation_quality_scorer.llm_service.chat", return_value=mock_response
        ):
            scorer = ConversationQualityScorer(db_session)
            results = await scorer.score_conversation(conv.id)

        assert len(results) == 2
        assert all(r.overall_score is not None for r in results)

    async def test_aggregates_to_conversation(self, db_session: AsyncSession) -> None:
        """Aggregates turn scores to conversation-level."""
        conv = await _create_conversation(db_session)
        await _create_message(db_session, conv.id, "user", "Q1")
        await _create_message(db_session, conv.id, "assistant", "A1")

        mock_response = AsyncMock()
        mock_response.content = json.dumps(
            {
                "helpfulness": 0.9,
                "coherence": 0.8,
                "instruction_adherence": 0.7,
                "topic": "billing",
                "reasoning": "Ok.",
            }
        )

        with patch(
            "hecate.services.ops_center.conversation_quality_scorer.llm_service.chat", return_value=mock_response
        ):
            scorer = ConversationQualityScorer(db_session)
            await scorer.score_conversation(conv.id)

        await db_session.refresh(conv)
        assert conv.quality_score is not None
        assert conv.quality_min_score is not None
        assert conv.topic == "billing"
        assert conv.quality_metrics is not None
