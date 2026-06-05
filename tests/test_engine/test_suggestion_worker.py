from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

from hecate.engine.workers.suggestion_worker import SuggestionWorker


@dataclass
class FakeSuggestionResult:
    questions: list[str]


class TestSuggestionWorker:
    async def test_no_suggestion_service(self) -> None:
        worker = SuggestionWorker(suggestion_service=None)
        result = await worker.execute(
            node_id="suggest",
            node_config={},
            channel_snapshot={},
        )
        assert result.channel_updates["suggested_questions"] == []
        assert result.channel_updates["messages"] == []

    async def test_generate_opening(self) -> None:
        mock_service = MagicMock()
        mock_service.generate_opening = AsyncMock(
            return_value=FakeSuggestionResult(questions=["What can you do?", "Help me code"])
        )
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={"generate_opening": True, "agent_persona": "Helpful assistant"},
            channel_snapshot={},
        )
        assert result.channel_updates["content"] == "Helpful assistant"
        assert result.channel_updates["suggested_questions"] == ["What can you do?", "Help me code"]
        assert len(result.channel_updates["messages"]) == 1
        assert result.channel_updates["messages"][0]["role"] == "assistant"

    async def test_generate_opening_default_persona(self) -> None:
        mock_service = MagicMock()
        mock_service.generate_opening = AsyncMock(return_value=FakeSuggestionResult(questions=["Q1"]))
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={"generate_opening": True},
            channel_snapshot={},
        )
        assert result.channel_updates["content"] == "Hello! How can I help you today?"

    async def test_generate_suggestions(self) -> None:
        mock_service = MagicMock()
        mock_service.generate_suggestions = AsyncMock(
            return_value=FakeSuggestionResult(questions=["Tell me more", "What else?"])
        )
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={"enable_suggestions": True},
            channel_snapshot={
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ]
            },
        )
        assert result.channel_updates["suggested_questions"] == ["Tell me more", "What else?"]
        mock_service.generate_suggestions.assert_called_once()

    async def test_suggestions_disabled(self) -> None:
        mock_service = MagicMock()
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={"enable_suggestions": False},
            channel_snapshot={"messages": [{"role": "assistant", "content": "Hi"}]},
        )
        assert result.channel_updates["suggested_questions"] == []

    async def test_suggestions_no_assistant_response(self) -> None:
        mock_service = MagicMock()
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={"enable_suggestions": True},
            channel_snapshot={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert result.channel_updates["suggested_questions"] == []

    async def test_generate_opening_failure(self) -> None:
        mock_service = MagicMock()
        mock_service.generate_opening = AsyncMock(side_effect=RuntimeError("API error"))
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={"generate_opening": True, "agent_persona": "Test"},
            channel_snapshot={},
        )
        assert result.channel_updates["suggested_questions"] == []

    async def test_generate_suggestions_failure(self) -> None:
        mock_service = MagicMock()
        mock_service.generate_suggestions = AsyncMock(side_effect=RuntimeError("API error"))
        worker = SuggestionWorker(suggestion_service=mock_service)
        result = await worker.execute(
            node_id="suggest",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi!"},
                ]
            },
        )
        assert result.channel_updates["suggested_questions"] == []
