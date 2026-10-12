"""OpenAI-compatible chat completions endpoint.

Implements ``POST /v1/chat/completions`` following the OpenAI Chat Completions API format.
All agent modes (chat, three_layer, workflow) now route through WorkflowExecutionService
and PregelRuntime for unified graph-based execution.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import uuid
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from hecate_llm.service import LLMResponse, llm_service
from hecate_llm.tool_calling import inject_tool_results, parse_tool_calls
from hecate_runtime.errors import ExecutionStoppedError, MaxSuperstepsError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from hecate.tools.tool.registry import ToolRegistry

from hecate_governance.claims import OutcomeUnknownError

from hecate.core.auth_context import AuthContext
from hecate.core.composition.entry_assembly import (
    build_tool_registry as _build_tool_registry,
)
from hecate.core.composition.entry_assembly import (
    load_agent_tools as _load_agent_tools,
)
from hecate.core.composition.governance import BudgetRefusedError
from hecate.core.composition.memory_policy import narrowed_tool_names
from hecate.core.database import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.core.output_schema import check_output_schema, output_schema_error
from hecate.models.agent import AgentModel
from hecate.models.model_provider import ModelProviderModel, ModelRegistryModel
from hecate.runtime.eventstore import EventStore
from hecate.runtime.guardrail import GuardrailAction
from hecate.runtime.middleware import Phase
from hecate.runtime.session_state import SessionStateStore

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
    output_schema: dict | None = Field(
        default=None,
        description="Optional JSON Schema the final assistant content must satisfy (step5e structured output)",
    )

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict | None) -> dict | None:
        """Reject malformed schemas before any execution is started."""
        if value is not None:
            check_output_schema(value)
        return value


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
            # Lazy: studio is a sibling domain — cross-domain access is
            # function-level only (no module-level structural coupling).
            from hecate.studio.session_lock import hold_lock_over_stream, session_lock_manager

            # Manual enter/exit instead of ``async with``: for streaming
            # responses the lock must outlive this frame — actual generation
            # happens while the client consumes the body iterator, so the
            # release is bound to the stream's lifetime.
            lock_ctx = session_lock_manager.acquire(session_id)
            lock_info = await lock_ctx.__aenter__()
            try:
                result = await _process_chat(
                    request,
                    db,
                    ctx.user_id,
                    ctx.workspace_id,
                    event_store,
                    session_state_store,
                    dlp_scanner,
                    cancel_check=_disconnect_check(http_request),
                )
            except BaseException:
                await lock_ctx.__aexit__(None, None, None)
                raise
            if isinstance(result, StreamingResponse):
                result.headers["X-Queue-Position"] = str(lock_info["queue_position"])
                result.headers["X-Queue-Wait-Ms"] = str(lock_info["wait_ms"])
                return hold_lock_over_stream(lock_ctx, result)
            await lock_ctx.__aexit__(None, None, None)
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
            request,
            db,
            ctx.user_id,
            ctx.workspace_id,
            event_store,
            session_state_store,
            dlp_scanner,
            cancel_check=_disconnect_check(http_request),
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
    cancel_check: Any | None = None,
) -> dict | StreamingResponse | JSONResponse:
    """Render terminal conditions consistently on both chat execution paths."""
    if request.stream and request.output_schema is not None:

        async def validated_stream():
            non_stream = request.model_copy(update={"stream": False})
            result = await _process_chat_impl(
                non_stream,
                db,
                user_id,
                workspace_id,
                event_store,
                session_state_store,
                dlp_scanner,
                preloaded_agent=preloaded_agent,
                cancel_check=cancel_check,
            )
            if isinstance(result, JSONResponse):
                yield f"data: {result.body.decode()}\n\n"
                yield "data: [DONE]\n\n"
                return
            if not isinstance(result, dict):
                raise TypeError("structured chat must return a non-streaming result")
            chunk = ChatCompletionChunk(
                model=result["model"],
                choices=[
                    ChatCompletionChunkChoice(
                        delta=ChatCompletionChunkDelta(content=result["choices"][0]["message"]["content"])
                    )
                ],
            )
            yield f"data: {json.dumps(chunk.model_dump())}\n\n"
            yield _format_done_chunk(result["model"])

        return StreamingResponse(_stop_safe_stream(validated_stream()), media_type="text/event-stream")
    try:
        return await _process_chat_impl(
            request,
            db,
            user_id,
            workspace_id,
            event_store,
            session_state_store,
            dlp_scanner,
            preloaded_agent=preloaded_agent,
            cancel_check=cancel_check,
        )
    except (ExecutionStoppedError, BudgetRefusedError, MaxSuperstepsError) as exc:
        return _stopped_response(_as_loop_stop(exc))


async def _process_chat_impl(
    request: ChatCompletionRequest,
    db: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
    event_store: EventStore | None = None,
    session_state_store: SessionStateStore | None = None,
    dlp_scanner: Any = None,
    *,
    preloaded_agent: AgentModel | None = None,
    cancel_check: Any | None = None,
) -> dict | StreamingResponse | JSONResponse:
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
        agent_tools = await _load_agent_tools(db, agent.tools or [], workspace_id=agent.workspace_id)
    effective_tools: list[dict[str, Any]] = list(agent_tools)
    if request.tools:
        client_names = {
            t.get("function", {}).get("name")
            for t in request.tools
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
        }
        effective_tools = [t for t in effective_tools if t.get("function", {}).get("name") not in client_names]
        effective_tools.extend(t for t in request.tools if isinstance(t, dict))

    # Memory-policy narrowing (memory-policy capability): applied after the
    # merge so a client-supplied tool cannot bypass the agent's policy —
    # the resolved policy may drop memory tools from the visible surface,
    # never add beyond the platform flag gate.
    if agent is not None and effective_tools:
        merged_names = [
            name for t in effective_tools if isinstance(t, dict) for name in [t.get("function", {}).get("name")] if name
        ]
        if merged_names:
            allowed = set(await narrowed_tool_names(db, agent.workspace_id, agent.id, merged_names))
            effective_tools = [
                t for t in effective_tools if isinstance(t, dict) and t.get("function", {}).get("name") in allowed
            ]
            agent_tools = [
                t for t in agent_tools if isinstance(t, dict) and t.get("function", {}).get("name") in allowed
            ]

    # Parse kb_ids if provided
    parsed_kb_ids: list[str] | None = None
    if request.kb_ids:
        parsed_kb_ids = request.kb_ids

    parsed_agent_id: str | uuid.UUID | None = None
    if request.agent_id:
        parsed_agent_id = request.agent_id
    elif agent_uuid is not None:
        parsed_agent_id = str(agent_uuid)

    from hecate.core.composition.governance import (
        GovernedModelService,
        GovernedToolRegistry,
        build_platform_governance,
    )

    governance = build_platform_governance(
        db,
        workspace_id=workspace_id or (agent.workspace_id if agent else None),
        agent_id=agent.id if agent else None,
        identity={"actor": str(user_id), "agent_principal": str(agent.id) if agent else None},
    )
    call_llm = _CheckedModelService(GovernedModelService(llm_service, governance), cancel_check)

    # Guardrail bundle (T0.2): assembled once and shared by both paths —
    # the direct loop and, when CHAT_TOOL_LOOP_ENGINE_ENABLED, the Pregel
    # engine ToolWorker. Reads the agent's guardrail_config and the
    # workspace's policy rule rows.
    bundle = None
    if agent_tools:
        from hecate.core.composition.guardrail_platform import assemble_guardrails

        bundle = await assemble_guardrails(
            db,
            workspace_id=workspace_id,
            agent_id=agent.id if agent is not None else None,
            guardrail_config=getattr(agent, "guardrail_config", None) if agent else None,
            event_store=event_store,
            session_id=uuid.UUID(request.session_id) if request.session_id else None,
            # 9.10 wiring: the scanner reaches the output/tool-result hooks,
            # so the documented 3-point scanning actually runs in the chain.
            dlp_scanner=dlp_scanner,
            user_id=user_id,
        )

    # B1b convergence (step5d routing): the effective path resolves per
    # request — workspace override (feature-flag tenant allowlist) beats
    # the global flag; a session with a recorded path keeps it. The direct
    # loop below stays as the rollback path.
    from hecate.execution.entry_routing import (
        PATH_ENGINE,
        read_session_path,
        record_session_path,
        resolve_chat_engine_path,
    )

    effective_workspace = workspace_id if workspace_id is not None else (agent.workspace_id if agent else None)
    session_path = await read_session_path(db, request.session_id)
    resolved_path = await resolve_chat_engine_path(db, effective_workspace, session_path=session_path)
    use_engine_loop = bool(agent_tools) and resolved_path == PATH_ENGINE
    if agent_tools:
        # Record affinity on the session row (best-effort) so continuations
        # never switch path mid-conversation.
        await record_session_path(db, request.session_id, resolved_path)
    if agent_tools and not use_engine_loop:
        # Agent-configured tools: drive the tool-calling loop directly —
        # the LLM proposes tool calls, the registry executes them, and results
        # feed back into the conversation (mirrors ConversationService).
        tool_registry = _build_tool_registry(
            db,
            skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None),
            workspace_id=agent.workspace_id if agent is not None else workspace_id or uuid.UUID(int=0),
        )
        tool_registry = GovernedToolRegistry(tool_registry, governance)
        provider_cfg = await _get_provider_config(db, effective_model)
        if request.stream:
            return StreamingResponse(
                _stop_safe_stream(
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
                        model_service=call_llm,
                        access_policy=bundle.access_policy,
                        tool_rules=bundle.rules,
                        approval_callback=bundle.approval_callback,
                        middleware_chains=bundle.middleware_chains,
                        denial_tracker=bundle.denial_tracker,
                        agent_id=agent.id if agent is not None else None,
                        workspace_id=agent.workspace_id if agent is not None else workspace_id,
                        cancel_check=cancel_check,
                        output_schema=request.output_schema,
                    )
                ),
                media_type="text/event-stream",
            )

        try:
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
                model_service=call_llm,
                access_policy=bundle.access_policy,
                tool_rules=bundle.rules,
                approval_callback=bundle.approval_callback,
                middleware_chains=bundle.middleware_chains,
                denial_tracker=bundle.denial_tracker,
                agent_id=agent.id if agent is not None else None,
                workspace_id=agent.workspace_id if agent is not None else workspace_id,
                cancel_check=cancel_check,
                output_schema=request.output_schema,
            )
        except _LoopStopError as stop:
            return _stopped_response(stop)
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

    # With the convergence flag on, tool agents execute here too — the
    # engine branch must also serve them (this also fixes the pre-existing
    # loss of agent-configured tools on the enhanced branch).
    use_enhanced = parsed_kb_ids or request.generate_opening or request.generate_suggestions or use_engine_loop

    if use_enhanced:
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.execution.entry_events import RunEventMapper
        from hecate.execution.entry_service import CorrelationInput, EntryExecutionService

        tool_registry = _build_tool_registry(
            db,
            skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None),
            workspace_id=agent.workspace_id if agent is not None else workspace_id or uuid.UUID(int=0),
        )

        port = create_runtime_port(
            db,
            _CheckedModelService(llm_service, cancel_check),
            tool_registry=tool_registry,
            workspace_id=workspace_id or (agent.workspace_id if agent is not None else None),
            agent_id=agent.id if agent is not None else None,
            identity={
                # Verified entry identity: subject from the server-side auth
                # context, agent attribution from the resolved agent row.
                "actor": str(user_id),
                "agent_principal": str(agent.id) if agent is not None else None,
            },
        )

        # step5d: the entry service owns assembly + Task/Run correlation;
        # this module keeps protocol adaptation only.
        entry = EntryExecutionService(
            port=port,
            entry_name="http-chat",
            db=db,
            event_store=event_store,
            checkpoint_store=session_state_store,
            access_policy=bundle.access_policy if bundle else None,
            approval_callback=bundle.approval_callback if bundle else None,
            tool_policy_rules=bundle.rules if bundle else None,
            middleware_chains=bundle.middleware_chains if bundle else None,
            denial_tracker=bundle.denial_tracker if bundle else None,
        )
        goal = next(
            (m.get("content", "")[:200] for m in msg_dicts if m.get("role") == "user" and m.get("content")),
            None,
        )
        correlation_input = CorrelationInput(
            workspace_id=effective_workspace,
            agent_id=agent.id if agent is not None else None,
            user_id=user_id,
            session_id=uuid.UUID(request.session_id) if request.session_id else None,
            goal=goal,
        )

        if request.stream and request.output_schema is None:

            async def _stream_with_workflow():
                outcome = await entry.execute(
                    agent_mode="chat",
                    messages=msg_dicts,
                    model=effective_model,
                    tools=effective_tools,
                    stream=True,
                    session_id=request.session_id,
                    agent_id=parsed_agent_id,
                    # Tenant scope for the engine's session-state persistence
                    # (checkpoint save on completion and on disconnect).
                    user_id=user_id,
                    kb_ids=parsed_kb_ids,
                    generate_opening=request.generate_opening,
                    enable_suggestions=request.generate_suggestions,
                    skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None),
                    correlation=correlation_input,
                )
                result_gen = outcome.result

                if isinstance(result_gen, dict):
                    yield _format_done_chunk(effective_model)
                    return

                # step5d event mapping: when the execution is correlated to a
                # run the adapter renders from platform envelopes (client-safe
                # payloads only); otherwise it falls back to the raw engine
                # stream — never silently claiming registration.
                correlation = outcome.correlation
                use_mapped = (
                    correlation.status == "registered"
                    and correlation.task_ref is not None
                    and correlation.run_ref is not None
                )
                if use_mapped:
                    mapper = RunEventMapper(correlation.task_ref, correlation.run_ref)
                    async for envelope in _map_stream(result_gen, mapper):
                        payload = envelope.payload
                        if payload.get("type") == "message":
                            delta_content = payload.get("content", "")
                            if not delta_content:
                                # Empty deltas (tool-call-only iterations) are
                                # not content — suppress rather than emit
                                # no-op chunks.
                                continue
                            chunk = ChatCompletionChunk(
                                model=effective_model,
                                choices=[
                                    ChatCompletionChunkChoice(
                                        delta=ChatCompletionChunkDelta(content=delta_content),
                                        finish_reason=None,
                                    )
                                ],
                            )
                            yield f"data: {json.dumps(chunk.model_dump())}\n\n"
                        elif payload.get("type") == "values":
                            suggested_questions = payload.get("suggested_questions")
                            if suggested_questions:
                                yield (
                                    f"data: {json.dumps({'type': 'suggestions', 'questions': suggested_questions})}\n\n"
                                )
                else:
                    logger.warning(
                        "Chat execution correlation missing (%s); rendering from raw engine stream",
                        correlation.reason,
                    )
                    async for event in result_gen:
                        if event.get("type") == "message":
                            delta_content = event.get("content", "")
                            if not delta_content:
                                continue
                            chunk = ChatCompletionChunk(
                                model=effective_model,
                                choices=[
                                    ChatCompletionChunkChoice(
                                        delta=ChatCompletionChunkDelta(content=delta_content),
                                        finish_reason=None,
                                    )
                                ],
                            )
                            yield f"data: {json.dumps(chunk.model_dump())}\n\n"
                        elif event.get("type") == "values":
                            state = event.get("state", {})
                            suggested_questions = state.get("suggested_questions")
                            if suggested_questions:
                                yield (
                                    f"data: {json.dumps({'type': 'suggestions', 'questions': suggested_questions})}\n\n"
                                )

                yield _format_done_chunk(effective_model)

            return StreamingResponse(
                _stop_safe_stream(_stream_with_workflow()),
                media_type="text/event-stream",
            )

        # Non-streaming with enhanced features
        outcome = await entry.execute(
            agent_mode="chat",
            messages=msg_dicts,
            model=effective_model,
            tools=effective_tools,
            stream=False,
            session_id=request.session_id,
            agent_id=parsed_agent_id,
            # Tenant scope for the engine's session-state persistence.
            user_id=user_id,
            kb_ids=parsed_kb_ids,
            generate_opening=request.generate_opening,
            enable_suggestions=request.generate_suggestions,
            skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None),
            correlation=correlation_input,
        )
        result = outcome.result

        if not isinstance(result, dict):
            msg = f"Expected dict result for non-streaming chat, got {type(result)}"
            raise TypeError(msg)

        if request.output_schema is not None:

            async def retry_final(feedback: str) -> Any:
                # Repair only the final answer: restarting the graph with tools
                # would allow a formatting retry to repeat business actions.
                return await call_llm.chat(
                    messages=msg_dicts
                    + [
                        {"role": "assistant", "content": result.get("content", "")},
                        {"role": "user", "content": _schema_feedback(feedback)},
                    ],
                    model=effective_model,
                    tools=None,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )

            violation = _validate_output_schema(result.get("content", ""), request.output_schema)
            if violation is not None:
                repaired = await _enforce_output_schema(
                    LLMResponse(content=result.get("content", ""), model=effective_model),
                    request.output_schema,
                    retry_final,
                )
                result = {**result, "content": repaired.content, "finish_reason": repaired.finish_reason or "stop"}

        suggested_questions = result.get("suggested_questions")

        response_payload = ChatCompletionResponse(
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
        # Correlation visibility: a missing registration is explicit in the
        # response instead of the platform claiming a record it does not
        # have. Registered executions stay OpenAI-clean (logged server-side).
        if outcome.correlation.status != "registered":
            response_payload["hecate_correlation"] = outcome.correlation.to_dict()
        return response_payload

    # Simple passthrough: no KB, no suggestions — use LLM directly for lowest latency
    provider_cfg = await _get_provider_config(db, effective_model)

    if request.stream and request.output_schema is None:
        return StreamingResponse(
            _stop_safe_stream(
                _stream_chat(
                    effective_model,
                    msg_dicts,
                    request.temperature,
                    request.max_tokens,
                    request.tools,
                    timeout=provider_cfg.get("timeout"),
                    num_retries=provider_cfg.get("num_retries"),
                    model_service=call_llm,
                )
            ),
            media_type="text/event-stream",
        )

    response = await call_llm.chat(
        messages=msg_dicts,
        model=effective_model,
        tools=request.tools,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        timeout=provider_cfg.get("timeout"),
        num_retries=provider_cfg.get("num_retries"),
    )

    if request.output_schema is not None:

        async def retry_plain(feedback: str) -> Any:
            return await call_llm.chat(
                messages=msg_dicts
                + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": _schema_feedback(feedback)},
                ],
                model=effective_model,
                tools=None,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=provider_cfg.get("timeout"),
                num_retries=provider_cfg.get("num_retries"),
            )

        response = await _enforce_output_schema(response, request.output_schema, retry_plain)
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


async def _map_stream(result_gen, mapper):
    """Map raw engine stream events to contract envelopes (step5d)."""
    async for event in result_gen:
        yield mapper.map_stream_event(event)


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
    model_service: Any = None,
):
    """Stream chat completion chunks via LiteLLM (simple passthrough).

    Yields:
        str: SSE-formatted chunks.
    """
    async for chunk in (model_service or llm_service).chat_stream(
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


def _history_ref(messages: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def _resume_pending_tool_turn(messages, registry, session_id, *, cancel_check=None, **tool_options):
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        tail = messages[index + 1 :]
        if any(item.get("role") != "tool" for item in tail):
            return messages
        done = {item.get("tool_call_id") for item in tail}
        pending = [tc for tc in parse_tool_calls(message["tool_calls"]) if tc["id"] not in done]
        if not pending:
            return messages
        await _CheckedModelService(None, cancel_check)._check()
        prefix = messages[:index]
        results = await _execute_tool_calls(
            registry,
            pending,
            session_id,
            turn_index=_count_tool_turns(prefix),
            history_ref=_history_ref(prefix),
            **tool_options,
        )
        return inject_tool_results(messages, message["tool_calls"], results)
    return messages


def _count_tool_turns(messages: list[dict[str, Any]]) -> int:
    """Number of assistant tool-call turns already in the conversation.

    Persisted history makes this stable across restarts and re-entry, unlike
    the model-generated ``tool_call.id``. Used as the round counter in the
    replay identity.
    """
    return sum(1 for m in messages if m.get("role") == "assistant" and m.get("tool_calls"))


def _replay_invocation_id(session_id: str | None, turn_index: int, tool_name: str, arguments: dict[str, Any]) -> str:
    """Logical-call identity for the direct loop (step5e replay).

    Stable across restarts for the same logical call: session + round +
    tool + normalized-argument digest. Distinct arguments produce distinct
    identities without colliding on the round.
    """
    import hashlib
    import json as _json

    digest = hashlib.sha256(
        _json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
    ).hexdigest()
    return f"chat:{session_id or 'anon'}:{turn_index}:{tool_name}:{digest[:16]}"


def _validate_output_schema(content: Any, output_schema: dict | None) -> str | None:
    """First JSON-Schema violation for the final assistant content, if any.

    Content is expected to be a JSON document when a schema is declared;
    non-JSON content fails with the parse error. Returns None when valid or
    when no schema is declared.
    """
    if output_schema is None:
        return None
    import json as _json

    if isinstance(content, str):
        try:
            document = _json.loads(content)
        except _json.JSONDecodeError as exc:
            return f"final answer is not valid JSON: {exc.msg} at position {exc.pos}"
    else:
        document = content
    return output_schema_error(document, output_schema)


async def _enforce_output_schema(response: Any, output_schema: dict | None, reinvoke) -> Any:
    """Validate the final answer against the declared schema.

    ``reinvoke(feedback_message)`` re-runs the model round exactly once with
    the violation fed back; a second violation fails with the invalid_output
    reason. Without a declared schema the response passes through unchanged.
    """
    violation = _validate_output_schema(response.content, output_schema)
    if violation is None:
        return response
    retry_response = await reinvoke(violation)
    if retry_response.tool_calls:
        raise _LoopStopError("invalid_output", f"output repair requested new tools; initial violation: {violation}")
    retry_violation = _validate_output_schema(retry_response.content, output_schema)
    if retry_violation is not None:
        raise _LoopStopError(
            "invalid_output",
            f"final answer violates the declared output schema after retry: {violation}",
        )
    return retry_response


def _disconnect_check(http_request: Request):
    """Cancel check bound to a starlette request's disconnect state."""

    async def _check() -> bool:
        try:
            return bool(await http_request.is_disconnected())
        except Exception:  # noqa: BLE001 - transport introspection must not break the loop
            return False

    return _check


def _stopped_response(stop: _LoopStopError) -> JSONResponse:
    """409 with the machine-readable stop category for non-stream clients."""
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "type": "execution_stopped",
                "reason_category": stop.reason_category,
                "detail": stop.detail,
                "action_ref": stop.action_ref,
            }
        },
    )


class _LoopStopError(ExecutionStoppedError):
    """Terminal chat condition rendered as a machine-readable stop."""


def _as_loop_stop(exc: Exception) -> _LoopStopError:
    if isinstance(exc, ExecutionStoppedError):
        return _LoopStopError(exc.reason_category, exc.detail, exc.action_ref)
    if isinstance(exc, BudgetRefusedError):
        return _LoopStopError("budget_exhausted", str(exc))
    return _LoopStopError("iteration_limit", str(exc))


async def _stop_safe_stream(source):
    try:
        async for chunk in source:
            yield chunk
    except (ExecutionStoppedError, BudgetRefusedError, MaxSuperstepsError) as exc:
        stop = _as_loop_stop(exc)
        payload = {
            "error": {
                "type": "execution_stopped",
                "reason_category": stop.reason_category,
                "detail": stop.detail,
                "action_ref": stop.action_ref,
            }
        }
        yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"


def _schema_feedback(violation: str) -> str:
    return f"Your response violates the required output schema: {violation}. Respond again satisfying the schema."


class _CheckedModelService:
    """Check cancellation and classify budget refusal at every model boundary."""

    def __init__(self, service: Any, cancel_check: Any = None) -> None:
        self._service = service
        self._cancel_check = cancel_check

    async def _check(self) -> None:
        if self._cancel_check is not None:
            signal = self._cancel_check()
            if inspect.isawaitable(signal):
                signal = await signal
            if signal:
                raise _LoopStopError("cancelled", "caller cancelled or disconnected before the next model round")

    async def chat(self, **kwargs: Any) -> Any:
        """Check the caller before invoking a metered non-streaming round."""
        await self._check()
        try:
            return await self._service.chat(**kwargs)
        except BudgetRefusedError as exc:
            raise _LoopStopError("budget_exhausted", str(exc)) from exc

    async def chat_stream(self, **kwargs: Any):
        """Handle budget refusal raised while the provider generator advances."""
        await self._check()
        try:
            async for chunk in self._service.chat_stream(**kwargs):
                yield chunk
        except BudgetRefusedError as exc:
            raise _LoopStopError("budget_exhausted", str(exc)) from exc


def _is_unknown_outcome_error(exc: BaseException) -> bool:
    """Whether the exception leaves the tool outcome unknowable.

    Matches the explicit governance exception and the duck-typed
    ``outcome_unknown`` flag the runtime receipt classifier recognizes.
    """
    if isinstance(exc, OutcomeUnknownError):
        return True
    return bool(getattr(exc, "outcome_unknown", False))


async def _execute_tool_calls(
    tool_registry: ToolRegistry,
    tool_calls: list[dict[str, Any]],
    session_id: str | None,
    *,
    turn_index: int = 0,
    history_ref: str | None = None,
    access_policy: Any | None = None,
    tool_rules: list | None = None,
    approval_callback: Any | None = None,
    risk_overrides: dict[str, str] | None = None,
    middleware_chains: dict | None = None,
    denial_tracker: Any | None = None,
    agent_id: Any | None = None,
    workspace_id: Any | None = None,
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
    from hecate.runtime.tool_access import AccessDecision

    risk_overrides = risk_overrides or {}

    async def _execute_one(tc: dict[str, Any]) -> dict[str, Any]:
        """Run guardrails + execution for a single tool call.

        Independent for each call so the outer caller can dispatch all calls
        concurrently (see ``asyncio.gather`` below).
        """
        # step5e replay identity: stable across restarts (turn index comes
        # from the persisted history; the digest from the normalized args).
        # The model-generated tc.id is display-only and never part of the
        # identity, so a re-entry re-derives the same logical-call key.
        tool_context: dict[str, Any] = {
            "session_id": session_id or "",
            "invocation_id": _replay_invocation_id(
                session_id, turn_index, tc.get("name", ""), tc.get("arguments") or {}
            ),
        }
        if history_ref is not None:
            tool_context["invocation_id"] += f":{history_ref}"
        if hasattr(tool_registry, "completed_result"):
            try:
                replayed, result = await tool_registry.completed_result(
                    tc["name"], tc.get("arguments") or {}, tool_context
                )
                if not replayed and session_id:
                    legacy_context = {**tool_context, "invocation_id": f"chat:{session_id}:{tc['id']}"}
                    replayed, result = await tool_registry.completed_result(
                        tc["name"], tc.get("arguments") or {}, legacy_context
                    )
                if replayed:
                    return {"tool_call_id": tc["id"], "result": result, "is_error": False}
            except Exception as exc:
                if _is_unknown_outcome_error(exc):
                    raise _LoopStopError("needs_review", str(exc), tool_context["invocation_id"]) from exc
                raise
        approval_ref = None
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
                return {
                    "tool_call_id": tc_id,
                    "result": "Tool denied by access policy",
                    "is_error": True,
                }
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
                    return {
                        "tool_call_id": tc["id"],
                        "result": f"Tool blocked: {pre_decision.reason}",
                        "is_error": True,
                    }
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
                return {
                    "tool_call_id": tc["id"],
                    "result": "Tool denied by access policy",
                    "is_error": True,
                }
            if decision == AccessDecision.REQUIRE_APPROVAL:
                if approval_callback is None:
                    if denial_tracker is not None and tc_id:
                        denial_tracker.deny(tc_id)
                    return {
                        "tool_call_id": tc["id"],
                        "result": "Tool requires approval but no callback configured",
                        "is_error": True,
                    }
                approval = await approval_callback.request_approval(
                    tool_name=tc_name,
                    arguments=tc_args,
                    risk_level=risk_level,
                    context={"session_id": session_id or "", "tool_call_id": tc_id},
                )
                approval_ref = approval.approval_ref
                if not approval.approved:
                    if denial_tracker is not None and tc_id:
                        denial_tracker.deny(tc_id)
                    return {
                        "tool_call_id": tc["id"],
                        "result": f"Tool call rejected: {approval.reason}",
                        "is_error": True,
                    }
        tool_context.update({"approval_ref": approval_ref, "tool_policy_rules": tool_rules or []})
        if agent_id is not None:
            tool_context["agent_id"] = str(agent_id)
        if workspace_id is not None:
            tool_context["workspace_id"] = str(workspace_id)
        try:
            result = await tool_registry.execute(
                tc["name"],
                tc.get("arguments") or {},
                context=tool_context,
            )
            return {"tool_call_id": tc["id"], "result": result, "is_error": False}
        except Exception as exc:
            # step5e stop semantics: an unknown outcome must terminate the
            # conversation (needs_review) — it is NEVER stringified into a
            # tool message and fed back to the model, because the model would
            # keep reasoning past an effect whose result is not established.
            if _is_unknown_outcome_error(exc):
                raise _LoopStopError(
                    "needs_review",
                    str(exc),
                    action_ref=tool_context.get("invocation_id"),
                ) from exc
            logger.warning("Tool '%s' execution failed: %s", tc["name"], exc)
            return {"tool_call_id": tc["id"], "result": str(exc), "is_error": True}

    outcomes = await asyncio.gather(*(_execute_one(tc) for tc in tool_calls), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return [outcome for outcome in outcomes if isinstance(outcome, dict)]


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
    model_service: Any = None,
    max_iterations: int = 5,
    access_policy: Any | None = None,
    tool_rules: list | None = None,
    approval_callback: Any | None = None,
    middleware_chains: dict | None = None,
    denial_tracker: Any | None = None,
    agent_id: Any | None = None,
    workspace_id: Any | None = None,
    cancel_check: Any | None = None,
    output_schema: dict | None = None,
) -> LLMResponse:
    """Run a non-streaming chat with a tool-calling loop.

    Iterates: the LLM proposes tool calls, the registry executes them, results
    are injected back into the conversation, and the LLM responds again. Stops
    when the LLM returns a final answer without tool calls.

    Raises:
        _LoopStop: on unknown tool outcome (needs_review), metered-budget
            refusal (budget_exhausted) or caller cancellation (cancelled).
            Unknown outcomes are never fed back to the model.

    Returns:
        The validated final LLM response. The iteration limit raises an explicit stop.
    """
    current_messages = list(messages)
    # step5e replay identity: the round counter is anchored to the persisted
    # history so a re-entry (restart/continuation) resumes the same logical
    # turn numbering instead of restarting from zero.
    if session_id is None:
        session_id = uuid.uuid4().hex
    current_messages = await _resume_pending_tool_turn(
        current_messages,
        tool_registry,
        session_id,
        cancel_check=cancel_check,
        access_policy=access_policy,
        tool_rules=tool_rules,
        approval_callback=approval_callback,
        middleware_chains=middleware_chains,
        denial_tracker=denial_tracker,
        agent_id=agent_id,
        workspace_id=workspace_id,
    )
    checked_model = (
        model_service
        if isinstance(model_service, _CheckedModelService)
        else _CheckedModelService(model_service or llm_service, cancel_check)
    )
    for _ in range(max_iterations):
        try:
            response = await checked_model.chat(
                messages=current_messages,
                model=model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                num_retries=num_retries,
            )
        except BudgetRefusedError as exc:
            raise _LoopStopError("budget_exhausted", str(exc)) from exc
        if not response.tool_calls:
            if output_schema is not None:
                bound_messages = current_messages
                bound_response = response

                async def reinvoke(feedback: str, _messages=bound_messages, _response=bound_response) -> Any:
                    _messages.append({"role": "assistant", "content": _response.content})
                    _messages.append(
                        {
                            "role": "user",
                            "content": (_schema_feedback(feedback)),
                        }
                    )
                    return await checked_model.chat(
                        messages=_messages,
                        model=model,
                        tools=None,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        num_retries=num_retries,
                    )

                response = await _enforce_output_schema(response, output_schema, reinvoke)
            return response
        tool_calls = parse_tool_calls(response.tool_calls)
        tool_results = await _execute_tool_calls(
            tool_registry,
            tool_calls,
            session_id,
            turn_index=_count_tool_turns(current_messages),
            history_ref=_history_ref(current_messages),
            access_policy=access_policy,
            tool_rules=tool_rules,
            approval_callback=approval_callback,
            middleware_chains=middleware_chains,
            denial_tracker=denial_tracker,
            agent_id=agent_id,
            workspace_id=workspace_id,
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
    raise _LoopStopError("iteration_limit", "tool loop reached its iteration limit")


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
    model_service: Any = None,
    max_iterations: int = 5,
    access_policy: Any | None = None,
    tool_rules: list | None = None,
    approval_callback: Any | None = None,
    middleware_chains: dict | None = None,
    denial_tracker: Any | None = None,
    agent_id: Any | None = None,
    workspace_id: Any | None = None,
    cancel_check: Any | None = None,
    output_schema: dict | None = None,
):
    """Stream a chat with a tool-calling loop.

    Intermediate tool iterations are executed silently; only the final LLM
    answer is streamed as SSE chunks (mirrors ConversationService._stream_chat).

    Yields:
        str: SSE-formatted chunks, ending with a done chunk and [DONE].
    """
    if output_schema is not None:
        response = await _chat_with_tools(
            messages,
            model,
            tools,
            tool_registry,
            temperature,
            max_tokens,
            session_id=session_id,
            timeout=timeout,
            num_retries=num_retries,
            model_service=model_service,
            max_iterations=max_iterations,
            access_policy=access_policy,
            tool_rules=tool_rules,
            approval_callback=approval_callback,
            middleware_chains=middleware_chains,
            denial_tracker=denial_tracker,
            agent_id=agent_id,
            workspace_id=workspace_id,
            cancel_check=cancel_check,
            output_schema=output_schema,
        )
        chunk = ChatCompletionChunk(
            model=model, choices=[ChatCompletionChunkChoice(delta=ChatCompletionChunkDelta(content=response.content))]
        )
        yield f"data: {json.dumps(chunk.model_dump())}\n\n"
        yield _format_done_chunk(model)
        return
    current_messages = list(messages)
    if session_id is None:
        session_id = uuid.uuid4().hex
    current_messages = await _resume_pending_tool_turn(
        current_messages,
        tool_registry,
        session_id,
        cancel_check=cancel_check,
        access_policy=access_policy,
        tool_rules=tool_rules,
        approval_callback=approval_callback,
        middleware_chains=middleware_chains,
        denial_tracker=denial_tracker,
        agent_id=agent_id,
        workspace_id=workspace_id,
    )
    checked_model = (
        model_service
        if isinstance(model_service, _CheckedModelService)
        else _CheckedModelService(model_service or llm_service, cancel_check)
    )
    for _ in range(max_iterations):
        tool_calls_buffer: list[dict[str, Any]] = []
        content_buffer: list[str] = []
        try:
            stream_iter = checked_model.chat_stream(
                messages=current_messages,
                model=model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                num_retries=num_retries,
            )
        except BudgetRefusedError as exc:
            raise _LoopStopError("budget_exhausted", str(exc)) from exc
        async for chunk in stream_iter:
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
            yield _format_done_chunk(model)
            return
        tool_calls = parse_tool_calls(tool_calls_buffer)
        tool_results = await _execute_tool_calls(
            tool_registry,
            tool_calls,
            session_id,
            turn_index=_count_tool_turns(current_messages),
            history_ref=_history_ref(current_messages),
            access_policy=access_policy,
            tool_rules=tool_rules,
            approval_callback=approval_callback,
            middleware_chains=middleware_chains,
            denial_tracker=denial_tracker,
            agent_id=agent_id,
            workspace_id=workspace_id,
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
    raise _LoopStopError("iteration_limit", "tool loop reached its iteration limit")
