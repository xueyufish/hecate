"""OpenAI-compatible chat completions endpoint.

Implements ``POST /v1/chat/completions`` following the OpenAI Chat Completions API format.
Supports both streaming (SSE) and non-streaming responses.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key

router = APIRouter()


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: str = Field(..., pattern="^(system|user|assistant|tool)$")
    content: str | None = None
    name: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """Request body for chat completions endpoint."""

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1)
    tools: list | None = None
    tool_choice: str | dict | None = None


class ChatCompletionChoice(BaseModel):
    """A single completion choice."""

    index: int = 0
    message: ChatMessage
    finish_reason: str | None = "stop"


class ChatCompletionUsage(BaseModel):
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """Response body for non-streaming chat completions."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(__import__("time").time()))
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage = Field(default_factory=ChatCompletionUsage)


class ChatCompletionChunkDelta(BaseModel):
    """Delta for streaming chunks."""

    role: str | None = None
    content: str | None = None


class ChatCompletionChunkChoice(BaseModel):
    """A single chunk choice."""

    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    """A single streaming chunk."""

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(__import__("time").time()))
    model: str
    choices: list[ChatCompletionChunkChoice]


@router.post("/chat/completions", response_model=None)
async def create_chat_completion(
    request: ChatCompletionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
):
    """Create a chat completion.

    Args:
        request: The chat completion request.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        StreamingResponse if stream=True, otherwise ChatCompletionResponse dict.

    Raises:
        HTTPException: 404 if model/agent not found.
    """
    if request.stream:
        return StreamingResponse(
            _stream_chat_completion(request, db),
            media_type="text/event-stream",
        )

    response_text = "Hello! I'm a placeholder response. The LLM service will be integrated in §6."

    return ChatCompletionResponse(
        model=request.model,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=response_text),
                finish_reason="stop",
            )
        ],
        usage=ChatCompletionUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
    ).model_dump()


async def _stream_chat_completion(
    request: ChatCompletionRequest,
    db: AsyncSession,
):
    """Generate streaming chat completion chunks.

    Args:
        request: The chat completion request.
        db: The async database session.

    Yields:
        str: SSE-formatted chunks.
    """
    response_text = "Hello! I'm a placeholder streaming response. The LLM service will be integrated in §6."

    for _i, char in enumerate(response_text):
        chunk = ChatCompletionChunk(
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    delta=ChatCompletionChunkDelta(content=char),
                    finish_reason=None,
                )
            ],
        )
        yield f"data: {json.dumps(chunk.model_dump())}\n\n"

    final_chunk = ChatCompletionChunk(
        model=request.model,
        choices=[
            ChatCompletionChunkChoice(
                delta=ChatCompletionChunkDelta(),
                finish_reason="stop",
            )
        ],
    )
    yield f"data: {json.dumps(final_chunk.model_dump())}\n\n"
    yield "data: [DONE]\n\n"
