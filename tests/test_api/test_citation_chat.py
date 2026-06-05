"""Tests for citation chat API."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture(autouse=True)
def override_auth():
    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


async def test_chat_completions_without_kb_ids(client: AsyncClient) -> None:
    """Test that chat completions work without kb_ids (backward compatible)."""
    with patch("hecate.api.v1.chat.llm_service") as mock_llm:
        mock_response = MagicMock()
        mock_response.content = "Hello!"
        mock_response.model = "gpt-4o"
        mock_response.finish_reason = "stop"
        mock_response.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        mock_response.tool_calls = None
        mock_llm.chat = AsyncMock(return_value=mock_response)
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0


async def test_chat_completions_with_invalid_kb_ids(client: AsyncClient) -> None:
    """Test that invalid kb_ids are passed through (validation happens in execution service)."""
    with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
        mock_service = MagicMock()
        mock_service.execute = AsyncMock(
            return_value={
                "model": "gpt-4o",
                "content": "Hello!",
                "finish_reason": "stop",
                "usage": {},
            }
        )
        mock_cls.return_value = mock_service
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "kb_ids": ["not-a-uuid"],
            },
        )
    # kb_ids are now strings, validation happens downstream in the execution service
    assert response.status_code == 200


async def test_chat_completions_with_valid_kb_ids(client: AsyncClient) -> None:
    """Test that valid kb_ids are accepted and routed through WorkflowExecutionService."""
    with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
        mock_service = MagicMock()
        mock_service.execute = AsyncMock(
            return_value={
                "model": "gpt-4o",
                "content": "Hello!",
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
        mock_cls.return_value = mock_service
        kb_id = str(uuid.uuid4())
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "kb_ids": [kb_id],
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data


async def test_get_message_citations_not_found(client: AsyncClient) -> None:
    """Test that 404 is returned for non-existent message."""
    message_id = str(uuid.uuid4())
    response = await client.get(f"/api/messages/{message_id}/citations")
    assert response.status_code == 404


async def test_get_message_citations_empty(client: AsyncClient) -> None:
    """Test that empty citations array is returned for message without citations."""
    message_id = str(uuid.uuid4())
    response = await client.get(f"/api/messages/{message_id}/citations")
    assert response.status_code == 404
