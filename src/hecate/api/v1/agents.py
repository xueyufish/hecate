"""Agent chat completions endpoint.

Provides ``POST /v1/agents/{agent_id}/chat/completions`` — the canonical
way to invoke a Hecate agent via an OpenAI-compatible request body. The
``agent_id`` in the URL identifies which agent to run; the agent's
``model_config.model`` is used as the underlying LLM and its ``persona``
is injected as the system prompt.

This replaces the earlier ``model: "agent/<UUID>"`` overloading of
``/v1/chat/completions`` with a dedicated URL-path entry point that
matches the convention used by Bedrock AgentCore, Salesforce
Agentforce, IBM watsonx Orchestrate, and Palantir AIP.

The request body is the OpenAI Chat Completions shape (same fields and
limits); the ``model`` field is accepted for OpenAI SDK compatibility
but ignored — ``agent_id`` in the URL is authoritative.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.api.v1.chat import (
    ChatCompletionRequest,
    ChatMessage,
    _process_chat,
)
from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.engine.eventstore import EventStore
from hecate.engine.session_state import SessionStateStore
from hecate.models.agent import AgentModel
from hecate.services.session_lock import session_lock_manager

router = APIRouter()


class AgentChatCompletionRequest(BaseModel):
    """Request body for ``POST /v1/agents/{agent_id}/chat/completions``.

    Shape mirrors :class:`ChatCompletionRequest` so the OpenAI Python SDK
    (``openai.OpenAI(base_url=..., api_key=...)``) can be pointed at this
    endpoint without code changes. The ``model`` field is optional and
    ignored — ``agent_id`` in the URL path is the authoritative identifier.
    """

    model: str | None = Field(
        default=None,
        description="Ignored. The agent_id in the URL is authoritative.",
    )
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1)
    tools: list | None = None
    tool_choice: str | dict | None = None
    kb_ids: list[str] | None = None
    session_id: str | None = Field(None, description="Session ID for sequential processing")
    agent_id: str | None = Field(None, description="Ignored when the agent_id is in the URL path.")
    generate_opening: bool = Field(default=False, description="Generate opening remarks with starter questions")
    generate_suggestions: bool = Field(default=False, description="Generate follow-up question suggestions")


def _request_to_chat_request(req: AgentChatCompletionRequest) -> ChatCompletionRequest:
    """Coerce an AgentChatCompletionRequest into a ChatCompletionRequest.

    The downstream ``_process_chat`` in chat.py reads ``request.model`` to
    determine the upstream LLM. For an agent call the caller has already
    pre-resolved the agent, so we pass the agent's ``model_config.model``
    indirectly through ``preloaded_agent`` and never read this body field.
    We therefore supply a placeholder that ``_process_chat`` will accept
    after the upfront ``agent/<UUID>`` reject passes.
    """
    return ChatCompletionRequest(
        model=req.model or "agent-resolved",  # placeholder; preloaded_agent wins
        messages=req.messages,
        stream=req.stream,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        tools=req.tools,
        tool_choice=req.tool_choice,
        kb_ids=req.kb_ids,
        session_id=req.session_id,
        agent_id=req.agent_id,
        generate_opening=req.generate_opening,
        generate_suggestions=req.generate_suggestions,
    )


async def _load_agent_or_404(agent_id: uuid.UUID, db: AsyncSession) -> AgentModel:
    """Look up an agent by UUID; 404 if missing or soft-deleted."""
    agent_row = await db.execute(select(AgentModel).where(AgentModel.id == agent_id, ~AgentModel.deleted))
    agent = agent_row.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return agent


@router.post("/agents/{agent_id}/chat/completions", response_model=None)
async def chat_completion_for_agent(
    agent_id: uuid.UUID,
    request: AgentChatCompletionRequest,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    session_state_store: Annotated[SessionStateStore, Depends(get_session_state_store)],
    http_request: Request,
):
    """Invoke a Hecate agent via an OpenAI-compatible chat completions body.

    The ``agent_id`` in the URL identifies which agent runs; its
    ``model_config.model`` is used as the LLM and its ``persona`` is
    injected as the system prompt. The body shape is identical to
    ``POST /v1/chat/completions``; the ``model`` field is accepted but
    ignored (the agent_id in the URL wins).

    Streaming is supported via ``"stream": true``; the response is
    Server-Sent Events in the OpenAI Chat Completions chunk format.

    Args:
        agent_id: The agent to invoke (path parameter).
        request: OpenAI-compatible request body.
        ctx: The authenticated context.
        db: The async database session.
        event_store: Wired ``EventStore`` singleton.
        session_state_store: Wired ``SessionStateStore`` singleton.
        http_request: The underlying FastAPI request (for app-state lookups).

    Returns:
        ``StreamingResponse`` if ``stream=true``, otherwise a dict matching
        the OpenAI ChatCompletionResponse shape.

    Raises:
        HTTPException: 404 if the agent does not exist; 408 if a session
            lock cannot be acquired in time.
    """
    agent = await _load_agent_or_404(agent_id, db)
    chat_request = _request_to_chat_request(request)

    # 9.10 wiring: resolve the boot-built DLP scanner (main.py lifespan
    # when DLP_ENABLED) here — the nested helpers don't carry the request.
    dlp_scanner = getattr(http_request.app.state, "dlp_scanner", None)

    if request.session_id:
        try:
            async with session_lock_manager.acquire(request.session_id) as lock_info:
                result = await _process_chat(
                    chat_request,
                    db,
                    ctx.user_id,
                    ctx.workspace_id,
                    event_store,
                    session_state_store,
                    dlp_scanner,
                    preloaded_agent=agent,
                )
                if isinstance(result, StreamingResponse):
                    result.headers["X-Queue-Position"] = str(lock_info["queue_position"])
                    result.headers["X-Queue-Wait-Ms"] = str(lock_info["wait_ms"])
                return result
        except TimeoutError:
            raise HTTPException(
                status_code=408,
                detail={
                    "error": {
                        "code": "QUEUE_TIMEOUT",
                        "message": "Message timed out waiting in queue. Please try again.",
                        "details": None,
                    }
                },
            ) from None
    return await _process_chat(
        chat_request,
        db,
        ctx.user_id,
        ctx.workspace_id,
        event_store,
        session_state_store,
        dlp_scanner,
        preloaded_agent=agent,
    )
