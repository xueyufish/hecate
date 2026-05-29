"""OpenAI-compatible chat completions endpoint.

Implements ``POST /v1/chat/completions`` following the OpenAI Chat Completions API format.
Supports both streaming (SSE) and non-streaming responses via LiteLLM.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.core.deps import get_current_user_id
from hecate.models.model_provider import ModelProviderModel, ModelRegistryModel
from hecate.services.llm.service import llm_service

logger = logging.getLogger(__name__)

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


def _messages_to_dicts(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Convert Pydantic ChatMessage list to dicts for LiteLLM."""
    result: list[dict[str, Any]] = []
    for m in messages:
        d: dict[str, Any] = {"role": m.role, "content": m.content or ""}
        if m.name:
            d["name"] = m.name
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        result.append(d)
    return result


_DEFAULT_PROVIDER_CONFIG = {"timeout": 30, "max_retries": 3}


async def _get_provider_config(db: AsyncSession, model: str) -> dict[str, Any]:
    """Look up provider-level timeout/retry config for a model.

    Queries model_registry → model_providers to find the provider config.
    Returns empty dict if model not in registry (let litellm use its defaults).

    Args:
        db: The async database session.
        model: The model identifier (e.g., "gpt-4o").

    Returns:
        Dict with optional ``timeout`` and ``num_retries`` keys.
    """
    stmt = (
        select(ModelProviderModel.config)
        .join(ModelRegistryModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
        .where(
            ModelRegistryModel.model_id == model,
            ModelRegistryModel.deleted_at.is_(None),
            ModelRegistryModel.is_enabled.is_(True),
            ModelProviderModel.deleted_at.is_(None),
            ModelProviderModel.is_enabled.is_(True),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if config is None:
        return {}

    resolved = {**_DEFAULT_PROVIDER_CONFIG, **config}
    return {
        "timeout": resolved["timeout"],
        "num_retries": resolved["max_retries"],
    }


@router.post("/chat/completions", response_model=None)
async def create_chat_completion(
    request: ChatCompletionRequest,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a chat completion via LiteLLM.

    Applies provider-level timeout/retry config when the model is found
    in the model registry.

    Args:
        request: The chat completion request.
        user_id: The authenticated user ID.
        db: The async database session.

    Returns:
        StreamingResponse if stream=True, otherwise ChatCompletionResponse dict.
    """
    msg_dicts = _messages_to_dicts(request.messages)
    provider_cfg = await _get_provider_config(db, request.model)

    if request.stream:
        return StreamingResponse(
            _stream_chat(
                request.model,
                msg_dicts,
                request.temperature,
                request.max_tokens,
                request.tools,
                timeout=provider_cfg.get("timeout"),
                num_retries=provider_cfg.get("num_retries"),
            ),
            media_type="text/event-stream",
        )

    response = await llm_service.chat(
        messages=msg_dicts,
        model=request.model,
        tools=request.tools,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        timeout=provider_cfg.get("timeout"),
        num_retries=provider_cfg.get("num_retries"),
    )

    return ChatCompletionResponse(
        model=response.model or request.model,
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                ),
                finish_reason=response.finish_reason or "stop",
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=response.usage.get("prompt_tokens", 0),
            completion_tokens=response.usage.get("completion_tokens", 0),
            total_tokens=response.usage.get("total_tokens", 0),
        ),
    ).model_dump()


async def _stream_chat(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list | None,
    timeout: float | None = None,
    num_retries: int | None = None,
):
    """Stream chat completion chunks via LiteLLM.

    Yields:
        str: SSE-formatted chunks.
    """
    async for chunk in llm_service.chat_stream(
        messages=messages,
        model=model,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        num_retries=num_retries,
    ):
        sse_chunk = ChatCompletionChunk(
            model=model,
            choices=[
                ChatCompletionChunkChoice(
                    delta=ChatCompletionChunkDelta(content=chunk.get("content")),
                    finish_reason=chunk.get("finish_reason"),
                )
            ],
        )
        yield f"data: {json.dumps(sse_chunk.model_dump())}\n\n"

    final_chunk = ChatCompletionChunk(
        model=model,
        choices=[
            ChatCompletionChunkChoice(
                delta=ChatCompletionChunkDelta(),
                finish_reason="stop",
            )
        ],
    )
    yield f"data: {json.dumps(final_chunk.model_dump())}\n\n"
    yield "data: [DONE]\n\n"
