"""Tests for opening remarks API integration."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from hecate.api.v1.chat import ChatCompletionRequest, ChatMessage
from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture(autouse=True)
def _override_user_auth():
    """Override user auth for all tests in this module."""

    async def _user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = _user_id
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


def test_chat_request_with_generate_opening_field():
    """Test that ChatCompletionRequest accepts generate_opening field."""
    request = ChatCompletionRequest(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
        generate_opening=True,
    )
    assert request.generate_opening is True
    assert request.generate_suggestions is False


def test_chat_request_with_generate_suggestions_field():
    """Test that ChatCompletionRequest accepts generate_suggestions field."""
    request = ChatCompletionRequest(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
        generate_suggestions=True,
    )
    assert request.generate_opening is False
    assert request.generate_suggestions is True


def test_chat_request_defaults():
    """Test that generate_opening and generate_suggestions default to False."""
    request = ChatCompletionRequest(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert request.generate_opening is False
    assert request.generate_suggestions is False


def test_chat_message_with_suggested_questions():
    """Test that ChatMessage accepts suggested_questions field."""
    message = ChatMessage(
        role="assistant",
        content="Hello!",
        suggested_questions=["What can you do?", "Tell me about yourself"],
    )
    assert message.suggested_questions == ["What can you do?", "Tell me about yourself"]


def test_chat_message_suggested_questions_default():
    """Test that suggested_questions defaults to None."""
    message = ChatMessage(role="assistant", content="Hello!")
    assert message.suggested_questions is None


@pytest.mark.asyncio
async def test_create_chat_completion_passes_opening_flags(client: AsyncClient):
    """Test that create_chat_completion passes generate_opening and generate_suggestions through the engine."""
    with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
        mock_service = MagicMock()
        mock_service.execute = AsyncMock(
            return_value={
                "content": "Hello! How can I help?",
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "finish_reason": "stop",
                "suggested_questions": ["What can you do?"],
            }
        )
        mock_cls.return_value = mock_service

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "generate_opening": True,
                "generate_suggestions": True,
            },
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_non_streaming_response_with_suggested_questions(client: AsyncClient):
    """Test that non-streaming response includes suggested_questions in ChatMessage."""
    with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
        mock_service = MagicMock()
        mock_service.execute = AsyncMock(
            return_value={
                "content": "Hello! How can I help?",
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "finish_reason": "stop",
                "suggested_questions": ["What can you do?", "Tell me about Hecate"],
            }
        )
        mock_cls.return_value = mock_service

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "generate_opening": True,
            },
        )
        assert response.status_code == 200
        data = response.json()

        message = data["choices"][0]["message"]
        assert message["suggested_questions"] == ["What can you do?", "Tell me about Hecate"]


@pytest.mark.asyncio
async def test_non_streaming_response_without_suggested_questions(client: AsyncClient):
    """Test that non-streaming response without suggestions has null suggested_questions."""
    with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
        mock_service = MagicMock()
        mock_service.execute = AsyncMock(
            return_value={
                "content": "Hello! How can I help?",
                "model": "gpt-4o",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "finish_reason": "stop",
            }
        )
        mock_cls.return_value = mock_service

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "generate_opening": True,
            },
        )
        assert response.status_code == 200
        data = response.json()

        message = data["choices"][0]["message"]
        assert message.get("suggested_questions") is None


@pytest.mark.asyncio
async def test_streaming_response_with_suggestions_event(client: AsyncClient):
    """Test that streaming with generate_opening is accepted by the API."""
    with patch("hecate.services.orchestration.engine_port_adapter.create_engine_port") as mock_create_port:
        mock_port = MagicMock()

        async def mock_llm_invoke(*args, **kwargs):
            yield "Hello! "
            yield "How can I help?"

        mock_port.llm_invoke = mock_llm_invoke
        mock_port.context_assemble = AsyncMock(return_value={"messages": [], "tools": None, "metadata": {}})
        mock_port.create_span = AsyncMock(return_value=None)
        mock_port.end_span = AsyncMock(return_value=None)
        mock_create_port.return_value = mock_port

        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
                "generate_opening": True,
            },
        )
        assert response.status_code == 200


@pytest.mark.asyncio
@patch("hecate.api.v1.chat.llm_service")
async def test_streaming_response_without_suggestions(mock_llm_service, client: AsyncClient):
    """Test that streaming without suggestions flag does not yield suggestions event."""

    async def mock_chat_stream(*args, **kwargs):
        yield {"content": "Hello!"}
        yield {"finish_reason": "stop"}

    mock_llm_service.chat_stream = mock_chat_stream

    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines()]

        suggestions_events = [line for line in lines if line.startswith("data: ") and '"suggestions"' in line]
        assert len(suggestions_events) == 0
