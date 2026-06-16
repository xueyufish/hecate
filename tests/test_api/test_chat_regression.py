from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture(autouse=True)
def _override_user_id():
    def override():
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


class TestChatRegression:
    async def test_non_streaming_basic(self, client):
        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(
                return_value=MagicMock(
                    content="Hi!",
                    model="gpt-4o",
                    tool_calls=None,
                    finish_reason="stop",
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            )
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
        assert data["choices"][0]["message"]["content"] == "Hi!"

    async def test_streaming_basic(self, client):
        with patch("hecate.api.v1.chat.llm_service") as mock_llm:

            async def fake_stream(*args, **kwargs):
                yield {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}
                yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

            mock_llm.chat_stream = fake_stream
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "stream": True,
                },
            )
        assert response.status_code == 200

    async def test_with_kb_ids_enhanced_path(self, client):
        with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
            mock_service = MagicMock()
            mock_service.execute = AsyncMock(
                return_value={
                    "model": "gpt-4o",
                    "content": "Based on docs...",
                    "finish_reason": "stop",
                    "usage": {},
                }
            )
            mock_cls.return_value = mock_service
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "What is Hecate?"}],
                    "kb_ids": ["kb-001"],
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "Based on docs..."

    async def test_with_generate_opening_enhanced_path(self, client):
        with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
            mock_service = MagicMock()
            mock_service.execute = AsyncMock(
                return_value={
                    "model": "gpt-4o",
                    "content": "Welcome!",
                    "finish_reason": "stop",
                    "usage": {},
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
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "Welcome!"
        assert data["choices"][0]["message"]["suggested_questions"] == ["What can you do?"]

    async def test_with_generate_suggestions_enhanced_path(self, client):
        with patch("hecate.api.v1.chat.WorkflowExecutionService") as mock_cls:
            mock_service = MagicMock()
            mock_service.execute = AsyncMock(
                return_value={
                    "model": "gpt-4o",
                    "content": "Here's the answer.",
                    "finish_reason": "stop",
                    "usage": {},
                    "suggested_questions": ["Tell me more", "What else?"],
                }
            )
            mock_cls.return_value = mock_service
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Explain X"}],
                    "generate_suggestions": True,
                },
            )
        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["suggested_questions"] == ["Tell me more", "What else?"]

    async def test_simple_passthrough_no_kb(self, client):
        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(
                return_value=MagicMock(
                    content="OK",
                    model="gpt-4o",
                    tool_calls=None,
                    finish_reason="stop",
                    usage={},
                )
            )
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert response.status_code == 200

    async def test_empty_messages(self, client):
        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(
                return_value=MagicMock(
                    content="Hello!",
                    model="gpt-4o",
                    tool_calls=None,
                    finish_reason="stop",
                    usage={},
                )
            )
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [],
                },
            )
        assert response.status_code == 200

    async def test_custom_model(self, client):
        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(
                return_value=MagicMock(
                    content="OK",
                    model="claude-3-opus",
                    tool_calls=None,
                    finish_reason="stop",
                    usage={},
                )
            )
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "claude-3-opus",
                    "messages": [{"role": "user", "content": "Hi"}],
                },
            )
        assert response.status_code == 200
