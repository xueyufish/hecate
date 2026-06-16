"""Tests for SuggestionService.

Validates both the LLM-based generation path (success + JSON parsing) and the
static fallback path (persona question extraction + generic fallback).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hecate.services.llm.service import LLMResponse
from hecate.services.suggestions.service import SuggestionService


@pytest.fixture
def mock_llm() -> AsyncMock:
    """Provide a mock LLMService with a chat method."""
    return AsyncMock()


@pytest.fixture
def service(mock_llm: AsyncMock) -> SuggestionService:
    """Provide a SuggestionService wired to the mock LLM."""
    return SuggestionService(llm_service=mock_llm, default_model="test-model")


class TestGenerateOpening:
    """Validate opening suggestion generation."""

    async def test_success_returns_parsed_questions(self, service: SuggestionService, mock_llm: AsyncMock) -> None:
        """On LLM success, questions are parsed from the JSON array response."""
        mock_llm.chat.return_value = LLMResponse(
            content='["What is Python?", "How do I start?", "Any tips?"]',
            model="test-model",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        result = await service.generate_opening(
            agent_persona="A helpful assistant",
            agent_capabilities=["Coding", "Debugging"],
        )

        assert result.questions == ["What is Python?", "How do I start?", "Any tips?"]
        assert result.model == "test-model"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    async def test_calls_llm_with_timeout(self, service: SuggestionService, mock_llm: AsyncMock) -> None:
        """The LLM is called with a 2-second timeout."""
        mock_llm.chat.return_value = LLMResponse(content='["Q1?"]', model="m", usage={})

        await service.generate_opening(agent_persona=None, agent_capabilities=[])

        call_kwargs = mock_llm.chat.call_args
        assert call_kwargs.kwargs["timeout"] == 2.0

    async def test_fallback_on_llm_failure(self, service: SuggestionService, mock_llm: AsyncMock) -> None:
        """When the LLM raises, questions are extracted from persona text."""
        mock_llm.chat.side_effect = RuntimeError("LLM unavailable")

        persona = "I help with coding. What do you need? Can I assist with debugging?"
        result = await service.generate_opening(agent_persona=persona, agent_capabilities=[])

        assert "What do you need?" in result.questions
        assert "Can I assist with debugging?" in result.questions
        assert result.model == "fallback"

    async def test_fallback_generic_when_no_persona(self, service: SuggestionService, mock_llm: AsyncMock) -> None:
        """When persona is None and LLM fails, generic questions are returned."""
        mock_llm.chat.side_effect = RuntimeError("fail")

        result = await service.generate_opening(agent_persona=None, agent_capabilities=[])

        assert len(result.questions) == 3
        assert "What can you help me with?" in result.questions

    async def test_fallback_generic_when_persona_no_questions(
        self, service: SuggestionService, mock_llm: AsyncMock
    ) -> None:
        """When persona has no '?' sentences and LLM fails, generic questions are returned."""
        mock_llm.chat.side_effect = RuntimeError("fail")

        result = await service.generate_opening(agent_persona="A helpful assistant.", agent_capabilities=[])

        assert result.questions == [
            "What can you help me with?",
            "How does this work?",
            "Can you explain that in more detail?",
        ]


class TestGenerateSuggestions:
    """Validate follow-up suggestion generation."""

    async def test_success_returns_parsed_questions(self, service: SuggestionService, mock_llm: AsyncMock) -> None:
        """On LLM success, follow-up questions are parsed from JSON response."""
        mock_llm.chat.return_value = LLMResponse(
            content='["Can you elaborate?", "What about alternatives?"]',
            model="test-model",
            usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        )

        result = await service.generate_suggestions(
            agent_persona="Helper",
            conversation_history="User: Hello\nAssistant: Hi!",
            current_response="Hi there!",
        )

        assert result.questions == ["Can you elaborate?", "What about alternatives?"]
        assert result.model == "test-model"

    async def test_fallback_on_llm_failure(self, service: SuggestionService, mock_llm: AsyncMock) -> None:
        """When the LLM raises for follow-up, persona questions are extracted."""
        mock_llm.chat.side_effect = RuntimeError("timeout")

        persona = "I answer questions. What else would you like to know?"
        result = await service.generate_suggestions(
            agent_persona=persona,
            conversation_history="",
            current_response="Some response",
        )

        assert "What else would you like to know?" in result.questions
        assert result.model == "fallback"


class TestParseJsonArray:
    """Validate JSON array parsing."""

    def test_valid_json_array(self) -> None:
        """A valid JSON array of strings is parsed correctly."""
        result = SuggestionService._parse_json_array('["a", "b", "c"]')
        assert result == ["a", "b", "c"]

    def test_non_string_items_are_coerced(self) -> None:
        """Non-string items in the array are coerced to strings."""
        result = SuggestionService._parse_json_array('[1, true, "x"]')
        assert result == ["1", "True", "x"]

    def test_invalid_json_raises(self) -> None:
        """Invalid JSON raises json.JSONDecodeError."""
        with pytest.raises(ValueError):
            SuggestionService._parse_json_array("not json")

    def test_non_array_json_raises(self) -> None:
        """A JSON object instead of array raises ValueError."""
        with pytest.raises(ValueError, match="Expected JSON array"):
            SuggestionService._parse_json_array('{"key": "value"}')


class TestFallback:
    """Validate static fallback logic."""

    def test_extracts_questions_from_persona(self) -> None:
        """Questions ending with '?' are extracted from persona text."""
        result = SuggestionService._fallback("How can I help? What do you need?")
        assert result.questions == ["How can I help?", "What do you need?"]
        assert result.model == "fallback"
        assert result.usage == {}

    def test_generic_questions_when_no_persona(self) -> None:
        """Generic questions are returned when persona is None."""
        result = SuggestionService._fallback(None)
        assert len(result.questions) == 3

    def test_generic_questions_when_persona_has_no_questions(self) -> None:
        """Generic questions are returned when persona has no '?' sentences."""
        result = SuggestionService._fallback("A helpful assistant.")
        assert len(result.questions) == 3
