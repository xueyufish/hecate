"""OpenAI-compatible chat completions endpoint.

Implements ``POST /v1/chat/completions`` following the OpenAI Chat Completions API format.
All agent modes (chat, three_layer, workflow) now route through WorkflowExecutionService
and PregelRuntime for unified graph-based execution.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.engine.eventstore import EventStore
from hecate.engine.guardrail import GuardrailAction
from hecate.engine.middleware import Phase
from hecate.engine.session_state import SessionStateStore
from hecate.models.agent import AgentModel
from hecate.models.model_provider import ModelProviderModel, ModelRegistryModel
from hecate.models.tool import ToolModel
from hecate.services.llm.service import LLMResponse, llm_service
from hecate.services.llm.tool_calling import format_tools_for_llm, inject_tool_results, parse_tool_calls
from hecate.services.session_lock import session_lock_manager
from hecate.services.tool.registry import ToolRegistry
from hecate.services.workflow.execution_service import WorkflowExecutionService

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: str = Field(..., pattern="^(system|user|assistant|tool)$")
    content: str | None = None
    name: str | None = None
    tool_calls: list | None = None
    tool_call_id: str | None = None
    annotations: list[dict[str, Any]] | None = None
    suggested_questions: list[str] | None = None


class ChatCompletionRequest(BaseModel):
    """Request body for chat completions endpoint."""

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1)
    tools: list | None = None
    tool_choice: str | dict | None = None
    kb_ids: list[str] | None = None
    session_id: str | None = Field(None, description="Session ID for sequential processing")
    agent_id: str | None = Field(None, description="Agent ID for skill loading")
    generate_opening: bool = Field(default=False, description="Generate opening remarks with starter questions")
    generate_suggestions: bool = Field(default=False, description="Generate follow-up question suggestions")


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


@router.post("/chat/completions", response_model=None)
async def create_chat_completion(
    request: ChatCompletionRequest,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    session_state_store: Annotated[SessionStateStore, Depends(get_session_state_store)],
    http_request: Request,
):
    """Create a chat completion via the unified execution engine.

    Routes all modes through WorkflowExecutionService and PregelRuntime.

    Args:
        request: The chat completion request.
        ctx: The authenticated context.
        db: The async database session.
        event_store: The wired ``EventStore`` singleton (app.state).
        session_state_store: The wired ``SessionStateStore`` singleton (app.state).

    Returns:
        StreamingResponse if stream=True, otherwise ChatCompletionResponse dict.
    """
    from fastapi import HTTPException

    session_id = request.session_id

    # 9.10 wiring: resolve the boot-built DLP scanner (main.py lifespan
    # when DLP_ENABLED) here — the nested helpers don't carry the request.
    dlp_scanner = getattr(http_request.app.state, "dlp_scanner", None)

    if session_id:
        try:
            async with session_lock_manager.acquire(session_id) as lock_info:
                result = await _process_chat(
                    request, db, ctx.user_id, ctx.workspace_id, event_store, session_state_store, dlp_scanner
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
    else:
        return await _process_chat(
            request, db, ctx.user_id, ctx.workspace_id, event_store, session_state_store, dlp_scanner
        )


async def _resolve_agent(
    agent_uuid: uuid.UUID,
    msg_dicts: list[dict[str, Any]],
    db: AsyncSession,
) -> tuple[str, list[dict[str, Any]], AgentModel]:
    """Load an agent by UUID and prepare the messages-with-persona context.

    Returns:
        Tuple of ``(effective_model, msg_dicts_with_persona, agent)``.
        ``effective_model`` comes from the agent's ``model_config.model``;
        ``msg_dicts_with_persona`` is a copy of the input with the agent's
        persona injected as the first system message when the caller did
        not supply one.
    """
    from fastapi import HTTPException

    agent_row = await db.execute(select(AgentModel).where(AgentModel.id == agent_uuid, ~AgentModel.deleted))
    agent = agent_row.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_uuid}")
    agent_cfg = agent.model_config_db or {}
    resolved_model = agent_cfg.get("model") if isinstance(agent_cfg, dict) else getattr(agent_cfg, "model", None)
    if not resolved_model:
        raise HTTPException(status_code=500, detail="Agent has no model_config.model configured")
    out_msgs = list(msg_dicts)
    if agent.persona and not any(m.get("role") == "system" for m in out_msgs):
        out_msgs.insert(0, {"role": "system", "content": agent.persona})
    return resolved_model, out_msgs, agent


async def _process_chat(
    request: ChatCompletionRequest,
    db: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
    event_store: EventStore | None = None,
    session_state_store: SessionStateStore | None = None,
    dlp_scanner: Any = None,
    *,
    preloaded_agent: AgentModel | None = None,
) -> dict | StreamingResponse:
    """Process a chat completion request via WorkflowExecutionService.

    Agent resolution is split across two entry points:

    - ``POST /v1/chat/completions`` — raw LLM call. ``request.model`` is the
      upstream model id; passing the legacy ``agent/<UUID>`` string here is
      rejected with a 400 redirecting to the agent endpoint.
    - ``POST /v1/agents/{agent_id}/chat/completions`` — the caller loads the
      agent from the URL and passes it via ``preloaded_agent``. ``request.model``
      is ignored; the agent's ``model_config.model`` is authoritative.
    """
    msg_dicts = _messages_to_dicts(request.messages)

    # Reject the legacy model-string agent routing. Use /v1/agents/{id}/chat/completions.
    # Only enforce on the raw /v1/chat/completions path; the agent endpoint
    # already passes preloaded_agent, and the placeholder model string it
    # forwards must not trip this check.
    if request.model.startswith("agent/") and preloaded_agent is None:
        from fastapi import HTTPException

        legacy_agent_id = request.model.removeprefix("agent/").strip()
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "DEPRECATED_ROUTING",
                    "message": (
                        "The 'agent/<UUID>' model-string routing is no longer supported. "
                        "Use POST /v1/agents/{agent_id}/chat/completions instead — the "
                        "agent_id now goes in the URL path."
                    ),
                    "details": {
                        "agent_id": legacy_agent_id,
                        "new_endpoint": f"/v1/agents/{legacy_agent_id}/chat/completions",
                    },
                }
            },
        )

    effective_model = request.model
    agent: AgentModel | None = preloaded_agent
    agent_uuid: uuid.UUID | None = agent.id if agent is not None else None
    if agent is not None:
        agent_cfg = agent.model_config_db or {}
        resolved_model = agent_cfg.get("model") if isinstance(agent_cfg, dict) else getattr(agent_cfg, "model", None)
        if resolved_model:
            effective_model = resolved_model
        if agent.persona and not any(m.get("role") == "system" for m in msg_dicts):
            msg_dicts.insert(0, {"role": "system", "content": agent.persona})

    # Load the agent's configured tools (builtin + DB) and merge with any
    # client-supplied tools; client tools take precedence on name conflict.
    agent_tools: list[dict[str, Any]] = []
    if agent is not None:
        agent_tools = await _load_agent_tools(db, agent.tools or [])
    effective_tools: list[dict[str, Any]] = list(agent_tools)
    if request.tools:
        client_names = {
            t.get("function", {}).get("name")
            for t in request.tools
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        }
        effective_tools = [t for t in effective_tools if t.get("function", {}).get("name") not in client_names]
        effective_tools.extend(t for t in request.tools if isinstance(t, dict))

    # Parse kb_ids if provided
    parsed_kb_ids: list[str] | None = None
    if request.kb_ids:
        parsed_kb_ids = request.kb_ids

    parsed_agent_id: str | uuid.UUID | None = None
    if request.agent_id:
        parsed_agent_id = request.agent_id
    elif agent_uuid is not None:
        parsed_agent_id = str(agent_uuid)

    if agent_tools:
        # Agent-configured tools: drive the tool-calling loop directly —
        # the LLM proposes tool calls, the registry executes them, and results
        # feed back into the conversation (mirrors ConversationService).
        tool_registry = _build_tool_registry(db)
        provider_cfg = await _get_provider_config(db, effective_model)
        # T0.2 (guardrail-upgrade-trio): assemble the guardrail bundle so path-A
        # direct tool loop is gated the same way the Pregel path is. Reads the
        # agent's guardrail_config and the workspace's policy rule rows.
        from hecate.services.security.guardrail_assembly import assemble_guardrails

        bundle = await assemble_guardrails(
            db,
            workspace_id=workspace_id,
            agent_id=parsed_agent_id if isinstance(parsed_agent_id, uuid.UUID) else None,
            guardrail_config=getattr(agent, "guardrail_config", None) if agent else None,
            event_store=event_store,
            session_id=uuid.UUID(request.session_id) if request.session_id else None,
            # 9.10 wiring: the scanner reaches the output/tool-result hooks,
            # so the documented 3-point scanning actually runs in the chain.
            dlp_scanner=dlp_scanner,
        )
        if request.stream:
            return StreamingResponse(
                _stream_chat_with_tools(
                    effective_model,
                    msg_dicts,
                    effective_tools,
                    tool_registry,
                    request.temperature,
                    request.max_tokens,
                    session_id=request.session_id,
                    timeout=provider_cfg.get("timeout"),
                    num_retries=provider_cfg.get("num_retries"),
                    access_policy=bundle.access_policy,
                    tool_rules=bundle.rules,
                    approval_callback=bundle.approval_callback,
                    middleware_chains=bundle.middleware_chains,
                    denial_tracker=bundle.denial_tracker,
                ),
                media_type="text/event-stream",
            )

        response = await _chat_with_tools(
            msg_dicts,
            effective_model,
            effective_tools,
            tool_registry,
            request.temperature,
            request.max_tokens,
            session_id=request.session_id,
            timeout=provider_cfg.get("timeout"),
            num_retries=provider_cfg.get("num_retries"),
            access_policy=bundle.access_policy,
            tool_rules=bundle.rules,
            approval_callback=bundle.approval_callback,
            middleware_chains=bundle.middleware_chains,
            denial_tracker=bundle.denial_tracker,
        )
        if response is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=500, detail="Tool call loop exceeded maximum iterations")

        return ChatCompletionResponse(
            model=response.model or effective_model,
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

    use_enhanced = parsed_kb_ids or request.generate_opening or request.generate_suggestions

    if use_enhanced:
        from hecate.services.orchestration.runtime_port_adapter import create_runtime_port

        tool_registry = _build_tool_registry(db)

        port = create_runtime_port(db, llm_service, tool_registry=tool_registry)

        exec_service = WorkflowExecutionService(
            port=port,
            db=db,
            event_store=event_store,
            checkpoint_store=session_state_store,
        )

        if request.stream:

            async def _stream_with_workflow():
                result_gen = await exec_service.execute(
                    agent_mode="chat",
                    messages=msg_dicts,
                    model=effective_model,
                    tools=request.tools,
                    stream=True,
                    session_id=request.session_id,
                    agent_id=parsed_agent_id,
                    kb_ids=parsed_kb_ids,
                    generate_opening=request.generate_opening,
                    enable_suggestions=request.generate_suggestions,
                )

                if isinstance(result_gen, dict):
                    yield _format_done_chunk(effective_model)
                    return

                async for event in result_gen:
                    if event.get("type") == "message":
                        chunk = ChatCompletionChunk(
                            model=effective_model,
                            choices=[
                                ChatCompletionChunkChoice(
                                    delta=ChatCompletionChunkDelta(content=event.get("content", "")),
                                    finish_reason=None,
                                )
                            ],
                        )
                        yield f"data: {json.dumps(chunk.model_dump())}\n\n"
                    elif event.get("type") == "values":
                        state = event.get("state", {})
                        suggested_questions = state.get("suggested_questions")
                        if suggested_questions:
                            yield f"data: {json.dumps({'type': 'suggestions', 'questions': suggested_questions})}\n\n"

                yield _format_done_chunk(effective_model)

            return StreamingResponse(
                _stream_with_workflow(),
                media_type="text/event-stream",
            )

        # Non-streaming with enhanced features
        result = await exec_service.execute(
            agent_mode="chat",
            messages=msg_dicts,
            model=effective_model,
            tools=request.tools,
            stream=False,
            session_id=request.session_id,
            agent_id=parsed_agent_id,
            kb_ids=parsed_kb_ids,
            generate_opening=request.generate_opening,
            enable_suggestions=request.generate_suggestions,
        )

        if not isinstance(result, dict):
            msg = f"Expected dict result for non-streaming chat, got {type(result)}"
            raise TypeError(msg)

        suggested_questions = result.get("suggested_questions")

        return ChatCompletionResponse(
            model=result.get("model", effective_model),
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(
                        role="assistant",
                        content=result.get("content", ""),
                        suggested_questions=suggested_questions,
                    ),
                    finish_reason=result.get("finish_reason", "stop"),
                )
            ],
            usage=ChatCompletionUsage(
                prompt_tokens=result.get("usage", {}).get("prompt_tokens", 0),
                completion_tokens=result.get("usage", {}).get("completion_tokens", 0),
                total_tokens=result.get("usage", {}).get("total_tokens", 0),
            ),
        ).model_dump()

    # Simple passthrough: no KB, no suggestions — use LLM directly for lowest latency
    provider_cfg = await _get_provider_config(db, effective_model)

    if request.stream:
        return StreamingResponse(
            _stream_chat(
                effective_model,
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
        model=effective_model,
        tools=request.tools,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        timeout=provider_cfg.get("timeout"),
        num_retries=provider_cfg.get("num_retries"),
    )

    return ChatCompletionResponse(
        model=response.model or effective_model,
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


def _format_done_chunk(model: str) -> str:
    """Format the final SSE chunk with finish_reason='stop'.

    Args:
        model: The model identifier.

    Returns:
        SSE-formatted string with done chunk.
    """
    final_chunk = ChatCompletionChunk(
        model=model,
        choices=[
            ChatCompletionChunkChoice(
                delta=ChatCompletionChunkDelta(),
                finish_reason="stop",
            )
        ],
    )
    return f"data: {json.dumps(final_chunk.model_dump())}\n\ndata: [DONE]\n\n"


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
    default_provider_config = {"timeout": 30, "max_retries": 3}

    stmt = (
        select(ModelProviderModel.config)
        .join(ModelRegistryModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
        .where(
            ModelRegistryModel.model_id == model,
            ~ModelRegistryModel.deleted,
            ModelRegistryModel.is_enabled.is_(True),
            ~ModelProviderModel.deleted,
            ModelProviderModel.is_enabled.is_(True),
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if config is None:
        return {}

    resolved = {**default_provider_config, **config}
    return {
        "timeout": resolved["timeout"],
        "num_retries": resolved["max_retries"],
    }


async def _stream_chat(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None,
    max_tokens: int | None,
    tools: list | None,
    timeout: float | None = None,
    num_retries: int | None = None,
):
    """Stream chat completion chunks via LiteLLM (simple passthrough).

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


async def _load_agent_tools(db: AsyncSession, tool_names: list[str]) -> list[dict[str, Any]]:
    """Resolve an agent's configured tools into OpenAI-format definitions.

    Builtin tool names resolve from the in-memory ``BUILTIN_TOOL_DEFINITIONS``;
    any other names are looked up in the ``ToolModel`` table.

    Args:
        db: The async database session.
        tool_names: Tool names configured on the agent.

    Returns:
        Tool definitions formatted for LLM function calling.
    """
    if not tool_names:
        return []
    from hecate.services.tool.builtin import BUILTIN_TOOL_DEFINITIONS

    definitions: list[dict[str, Any]] = []
    db_names: list[str] = []
    for name in tool_names:
        if name in BUILTIN_TOOL_DEFINITIONS:
            definitions.append({"name": name, **BUILTIN_TOOL_DEFINITIONS[name]})
        else:
            db_names.append(name)
    if db_names:
        result = await db.execute(select(ToolModel).where(ToolModel.name.in_(db_names), ~ToolModel.deleted))
        for tool in result.scalars().all():
            definitions.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                }
            )
    return format_tools_for_llm(definitions)


def _build_tool_registry(db: AsyncSession) -> ToolRegistry:
    """Construct a ToolRegistry wired to builtin + DB tools using app settings.

    Args:
        db: The async database session.

    Returns:
        A configured ToolRegistry.
    """
    from hecate.core.config import settings
    from hecate.services.tool.builtin import BuiltInToolExecutor
    from hecate.services.tool.search.factory import create_search_provider

    search_provider = create_search_provider(
        provider=settings.SEARCH_PROVIDER,
        api_key=settings.SEARCH_API_KEY,
    )
    builtin_executor = BuiltInToolExecutor(
        search_provider=search_provider,
        workspace_root=settings.WORKSPACE_ROOT,
    )
    return ToolRegistry(db=db, builtin_executor=builtin_executor)


async def _execute_tool_calls(
    tool_registry: ToolRegistry,
    tool_calls: list[dict[str, Any]],
    session_id: str | None,
    *,
    access_policy: Any | None = None,
    tool_rules: list | None = None,
    approval_callback: Any | None = None,
    risk_overrides: dict[str, str] | None = None,
    middleware_chains: dict | None = None,
    denial_tracker: Any | None = None,
) -> list[dict[str, Any]]:
    """Execute parsed tool calls, returning results for inject_tool_results.

    Args:
        tool_registry: The tool registry to execute against.
        tool_calls: Parsed tool calls (id, name, arguments).
        session_id: Optional session id passed as tool context.
        access_policy: Optional ``ToolAccessPolicy`` evaluated before execution
            (T0.2 path-A guardrail wiring). When ``None``, behavior matches the
            pre-change direct tool loop.
        tool_rules: ``ToolRule`` list used by ``access_policy``.
        approval_callback: Optional ``ApprovalCallback`` consulted when policy
            returns ``REQUIRE_APPROVAL``.
        risk_overrides: Per-tool-name risk level overrides; missing names
            default to ``"low"``.

    Returns:
        List of result dicts with tool_call_id, result, and is_error.
    """
    from hecate.engine.tool_access import AccessDecision

    results: list[dict[str, Any]] = []
    risk_overrides = risk_overrides or {}
    for tc in tool_calls:
        # Path-A guardrail evaluation (T0.2): same five-layer policy the
        # ToolWorker uses, kept consistent with the production chat surface.
        if access_policy is not None:
            tc_name = tc.get("name", "")
            tc_args = tc.get("arguments") or {}
            if isinstance(tc_args, str):
                import json

                try:
                    tc_args = json.loads(tc_args)
                except json.JSONDecodeError:
                    tc_args = {}
            risk_level = risk_overrides.get(tc_name, "low")
            # T3.3 (guardrail-upgrade-trio): monotonic-denial check. A denied
            # tool_call_id is refused without re-running the policy pipeline.
            tc_id = tc.get("id", "")
            if denial_tracker is not None and tc_id and denial_tracker.is_denied(tc_id):
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "result": "Tool denied by access policy",
                        "is_error": True,
                    }
                )
                continue
            # T1.5: pre-tool chain runs before access policy. When the chain
            # BLOCKs, the policy is skipped — chain decision is authoritative.
            pre_chain = (middleware_chains or {}).get(Phase.TOOL_PRE_EXECUTE)
            if pre_chain is not None:

                async def _pass(data):
                    return data

                pre_chain.set_handler(_pass)
                pre_data = {"name": tc_name, "arguments": tc_args, "context": None}
                pre_decision, _ = await pre_chain.run(pre_data)
                if pre_decision.action == GuardrailAction.BLOCK:
                    if denial_tracker is not None and tc_id:
                        denial_tracker.deny(tc_id)
                    results.append(
                        {
                            "tool_call_id": tc["id"],
                            "result": f"Tool blocked: {pre_decision.reason}",
                            "is_error": True,
                        }
                    )
                    continue
            tool_meta = {
                "name": tc_name,
                "risk_level": risk_level,
                "approval_required": risk_level == "critical",
                "sandbox_enabled": False,
            }
            decision = access_policy.evaluate(
                tool_meta,
                tool_rules or [],
                {"tool_name": tc_name},
                arguments=tc_args,
            )
            if decision == AccessDecision.DENY:
                if denial_tracker is not None and tc_id:
                    denial_tracker.deny(tc_id)
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "result": "Tool denied by access policy",
                        "is_error": True,
                    }
                )
                continue
            if decision == AccessDecision.REQUIRE_APPROVAL:
                if approval_callback is None:
                    if denial_tracker is not None and tc_id:
                        denial_tracker.deny(tc_id)
                    results.append(
                        {
                            "tool_call_id": tc["id"],
                            "result": "Tool requires approval but no callback configured",
                            "is_error": True,
                        }
                    )
                    continue
                approval = await approval_callback.request_approval(
                    tool_name=tc_name,
                    arguments=tc_args,
                    risk_level=risk_level,
                    context={"session_id": session_id or ""},
                )
                if not approval.approved:
                    if denial_tracker is not None and tc_id:
                        denial_tracker.deny(tc_id)
                    results.append(
                        {
                            "tool_call_id": tc["id"],
                            "result": f"Tool call rejected: {approval.reason}",
                            "is_error": True,
                        }
                    )
                    continue
        try:
            result = await tool_registry.execute(
                tc["name"],
                tc.get("arguments") or {},
                context={"session_id": session_id or ""},
            )
            results.append({"tool_call_id": tc["id"], "result": result, "is_error": False})
        except Exception as exc:
            logger.warning("Tool '%s' execution failed: %s", tc["name"], exc)
            results.append({"tool_call_id": tc["id"], "result": str(exc), "is_error": True})
    return results


async def _chat_with_tools(
    # TODO(event-sourced-state 1.3.19): this direct tool loop bypasses PregelRuntime and
    # therefore does NOT produce events in the engine log. Known boundary of log-as-truth
    # coverage. Long-term convergence: model this loop as an engine-internal subgraph
    # (1.3.18-style coordinator node) and pair with the waterfall middleware (1.3.5i E3).
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    temperature: float | None,
    max_tokens: int | None,
    session_id: str | None = None,
    timeout: float | None = None,
    num_retries: int | None = None,
    max_iterations: int = 5,
    access_policy: Any | None = None,
    tool_rules: list | None = None,
    approval_callback: Any | None = None,
    middleware_chains: dict | None = None,
    denial_tracker: Any | None = None,
) -> LLMResponse | None:
    """Run a non-streaming chat with a tool-calling loop.

    Iterates: the LLM proposes tool calls, the registry executes them, results
    are injected back into the conversation, and the LLM responds again. Stops
    when the LLM returns a final answer without tool calls.

    Returns:
        The final LLM response, or None when the loop exceeds max_iterations.
    """
    current_messages = list(messages)
    for _ in range(max_iterations):
        response = await llm_service.chat(
            messages=current_messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            num_retries=num_retries,
        )
        if not response.tool_calls:
            return response
        tool_calls = parse_tool_calls(response.tool_calls)
        tool_results = await _execute_tool_calls(
            tool_registry,
            tool_calls,
            session_id,
            access_policy=access_policy,
            tool_rules=tool_rules,
            approval_callback=approval_callback,
            middleware_chains=middleware_chains,
        )
        # Append the assistant tool-call message so the tool results are
        # well-formed (a tool message must follow its assistant tool_calls).
        current_messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": response.tool_calls,
            }
        )
        current_messages = inject_tool_results(current_messages, response.tool_calls, tool_results)
    return None


async def _stream_chat_with_tools(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_registry: ToolRegistry,
    temperature: float | None,
    max_tokens: int | None,
    session_id: str | None = None,
    timeout: float | None = None,
    num_retries: int | None = None,
    max_iterations: int = 5,
    access_policy: Any | None = None,
    tool_rules: list | None = None,
    approval_callback: Any | None = None,
    middleware_chains: dict | None = None,
    denial_tracker: Any | None = None,
):
    """Stream a chat with a tool-calling loop.

    Intermediate tool iterations are executed silently; only the final LLM
    answer is streamed as SSE chunks (mirrors ConversationService._stream_chat).

    Yields:
        str: SSE-formatted chunks, ending with a done chunk and [DONE].
    """
    current_messages = list(messages)
    for _ in range(max_iterations):
        tool_calls_buffer: list[dict[str, Any]] = []
        content_buffer: list[str] = []
        async for chunk in llm_service.chat_stream(
            messages=current_messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            num_retries=num_retries,
        ):
            content = chunk.get("content")
            if content:
                content_buffer.append(content)
                sse_chunk = ChatCompletionChunk(
                    model=model,
                    choices=[
                        ChatCompletionChunkChoice(
                            delta=ChatCompletionChunkDelta(content=content),
                            finish_reason=chunk.get("finish_reason"),
                        )
                    ],
                )
                yield f"data: {json.dumps(sse_chunk.model_dump())}\n\n"
            if chunk.get("tool_calls"):
                tool_calls_buffer.extend(chunk["tool_calls"])
        if not tool_calls_buffer:
            break
        tool_calls = parse_tool_calls(tool_calls_buffer)
        tool_results = await _execute_tool_calls(
            tool_registry,
            tool_calls,
            session_id,
            access_policy=access_policy,
            tool_rules=tool_rules,
            approval_callback=approval_callback,
            middleware_chains=middleware_chains,
        )
        # Append the assistant tool-call message so the tool results are
        # well-formed (a tool message must follow its assistant tool_calls).
        current_messages.append(
            {
                "role": "assistant",
                "content": "".join(content_buffer),
                "tool_calls": tool_calls_buffer,
            }
        )
        current_messages = inject_tool_results(current_messages, tool_calls_buffer, tool_results)
    yield _format_done_chunk(model)
