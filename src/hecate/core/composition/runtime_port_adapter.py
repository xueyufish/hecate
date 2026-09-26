"""Factory for creating RuntimePort adapters for production use.

Creates a concrete RuntimePort that wires engine calls to actual service
implementations (LLMService, tool execution, knowledge bases, etc.).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from hecate_ops.span_adapter import (
    create_otel_span,
    end_otel_span,
)
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.runtime.ports import RuntimePort, SpanContext

logger = logging.getLogger(__name__)

# Module-level holder for quota service — set during app startup
_quota_service_factory: Any = None

# Whitelisted call parameters forwarded from the agent model config to the
# LLM service — keeps arbitrary config keys out of the provider call.
_INVOKE_PARAM_KEYS = ("temperature", "max_tokens", "timeout", "num_retries")

_COST_PER_TOKEN = 0.00001


def _invoke_params(config: dict) -> dict[str, Any]:
    """Build the forwarded call kwargs from the agent model config."""
    return {key: config[key] for key in _INVOKE_PARAM_KEYS if config.get(key) is not None}


def _estimate_usage(messages: list[dict], output: str) -> dict[str, Any]:
    """Deterministic chunk-invariant token estimate for streams without
    provider usage: input and output are each measured once from their
    full text (never per-chunk, which silently rounds small chunks down
    to zero)."""
    input_chars = sum(len(str(m.get("content") or "")) for m in messages)
    prompt_tokens = -(-input_chars // 4)
    completion_tokens = -(-len(output) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "estimated": True,
    }


def set_quota_service_factory(factory: Any) -> None:
    """Register the QuotaService factory for cost recording."""
    global _quota_service_factory
    _quota_service_factory = factory


class _ProductionRuntimePort(RuntimePort):
    """Production RuntimePort adapter wiring engine calls to actual services.

    Delegates LLM calls to LLMService, tool calls to the tool registry,
    and knowledge queries to the knowledge base service.
    """

    def __init__(
        self,
        db: AsyncSession,
        llm_service: Any,
        tool_registry: Any = None,
        workspace_id: UUID | None = None,
        agent_id: UUID | None = None,
        pre_hook: Any = None,
        post_hook: Any = None,
        context_engine: Any = None,
    ) -> None:
        self._db = db
        self._llm_service = llm_service
        self._tool_registry = tool_registry
        self._workspace_id = workspace_id
        self._agent_id = agent_id
        self._pre_hook = pre_hook
        self._post_hook = post_hook
        self._context_engine = context_engine

    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        """Invoke LLM via LLMService in streaming mode.

        After streaming completes, records cost against quotas — provider
        usage when the stream carried it, a chunk-invariant estimate marked
        ``estimated`` otherwise.
        """
        model = config.get("model", "gpt-4o")
        tools = config.get("tools")

        content_parts: list[str] = []
        usage: dict[str, Any] | None = None
        async for chunk in self._llm_service.chat_stream(
            messages=messages,
            model=model,
            tools=tools,
            **_invoke_params(config),
        ):
            if chunk.get("usage"):
                usage = chunk["usage"]
                continue
            content = chunk.get("content", "") or ""
            if content:
                content_parts.append(content)
                yield content

        await self._record_cost(model, messages, "".join(content_parts), usage)

    async def llm_invoke_structured(
        self,
        messages: list[dict],
        config: dict,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Structured LLM invocation exposing tool_calls for the engine tool loop.

        Streams ``{"content": token, "tool_calls": None}`` chunks during streaming
        and accumulates LiteLLM's per-chunk tool_call deltas (keyed by ``index``)
        into complete tool_calls. Yields a final chunk
        ``{"content": None, "tool_calls": <list>}`` carrying the assembled calls,
        or ``tool_calls=None`` when the LLM did not request any tools.

        Args:
            messages: Conversation messages for the LLM.
            config: Provider-specific configuration; ``model`` and ``tools`` are read.

        Yields:
            Structured chunks dict per stream iteration; the final chunk carries
            the accumulated ``tool_calls`` (or ``None``).
        """
        model = config.get("model", "gpt-4o")
        tools = config.get("tools")

        tool_call_acc: dict[int, dict[str, Any]] = {}
        content_parts: list[str] = []
        usage: dict[str, Any] | None = None

        async for chunk in self._llm_service.chat_stream(
            messages=messages,
            model=model,
            tools=tools,
            **_invoke_params(config),
        ):
            if chunk.get("usage"):
                usage = chunk["usage"]
                continue
            content = chunk.get("content") or ""
            if content:
                content_parts.append(content)
                yield {"content": content, "tool_calls": None}

            delta_tool_calls = chunk.get("tool_calls")
            if delta_tool_calls:
                for tc in delta_tool_calls:
                    idx = getattr(tc, "index", 0) or 0
                    entry = tool_call_acc.setdefault(
                        idx,
                        {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    tc_id = getattr(tc, "id", None)
                    if tc_id:
                        entry["id"] = tc_id
                    tc_type = getattr(tc, "type", None)
                    if tc_type:
                        entry["type"] = tc_type
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        fn_name = getattr(fn, "name", None)
                        if fn_name:
                            entry["function"]["name"] = (entry["function"].get("name") or "") + fn_name
                        fn_args = getattr(fn, "arguments", None)
                        if fn_args:
                            entry["function"]["arguments"] = (entry["function"].get("arguments") or "") + fn_args

        final_tool_calls: list[dict[str, Any]] | None = None
        if tool_call_acc:
            final_tool_calls = [tool_call_acc[k] for k in sorted(tool_call_acc.keys())]

        yield {"content": None, "tool_calls": final_tool_calls}

        await self._record_cost(model, messages, "".join(content_parts), usage)

    async def _record_cost(self, model: str, messages: list[dict], output: str, usage: dict[str, Any] | None) -> None:
        """Record cost usage against quotas after LLM invocation.

        Uses the provider usage when the stream carried it; otherwise falls
        back to a chunk-invariant estimate (logged with ``estimated``).
        """
        resolved = dict(usage) if usage else _estimate_usage(messages, output)
        prompt = resolved.get("prompt_tokens", 0) or 0
        completion = resolved.get("completion_tokens", 0) or 0
        total = prompt + completion
        if resolved.get("estimated"):
            logger.info(
                "LLM cost recorded from estimate (provider usage unavailable): model=%s tokens=%d",
                model,
                total,
            )
        if _quota_service_factory is None or total == 0:
            return
        try:
            service = _quota_service_factory(self._db, self._workspace_id)
            cost_estimate = total * _COST_PER_TOKEN

            if self._workspace_id:
                await service.record_usage(
                    resource_type="cost",
                    scope="workspace",
                    scope_id=self._workspace_id,
                    window_type="monthly",
                    amount=cost_estimate,
                )
            if self._agent_id:
                await service.record_usage(
                    resource_type="cost",
                    scope="agent",
                    scope_id=self._agent_id,
                    window_type="monthly",
                    amount=cost_estimate,
                )
        except Exception:
            logger.debug("Cost recording skipped", exc_info=True)

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        """Execute a tool by name via ToolRegistry.

        Args:
            name: The registered tool name.
            args: Keyword arguments to pass to the tool.
            context: Optional execution context.

        Returns:
            The tool's return value.
        """
        if self._tool_registry is None:
            raise RuntimeError("ToolRegistry not configured in RuntimePort")
        return await self._tool_registry.execute(name, args, context)

    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        """Query knowledge bases via knowledge_base_service.

        Args:
            query: The search query string.
            kb_ids: UUIDs of the knowledge bases to search.

        Returns:
            A list of document chunk dicts.
        """
        from hecate.runtime.agent_execution_port import AgentExecutionPort

        port = AgentExecutionPort(self._db)
        return await port.knowledge_query(query, kb_ids)

    async def checkpoint_save(self, state: dict) -> UUID:
        """Save checkpoint via CheckpointStore."""
        return uuid.uuid4()

    async def checkpoint_load(self, checkpoint_id: UUID) -> dict:
        """Load checkpoint via CheckpointStore."""
        return {}

    async def conversation_load(self, session_id: UUID) -> list[dict]:
        """Load conversation history for a session."""
        return []

    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None:
        """Persist conversation messages for a session."""
        pass

    async def create_span(
        self,
        name: str,
        parent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContext | None:
        """Create an observability span via the shared OTel adapter."""
        return create_otel_span(name, parent_id=parent_id, attributes=attributes)

    async def end_span(
        self,
        span_id: str,
        output_data: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        """End an observability span via the shared OTel adapter."""
        end_otel_span(span_id, output_data=output_data, usage=usage)

    async def agent_execute(
        self,
        agent_id: UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
        agent_definition: Any | None = None,
    ) -> dict:
        """Execute an agent by ID via AgentExecutionPort.

        Args:
            agent_id: UUID of the agent to execute.
            messages: Conversation messages from parent graph.
            channel_snapshot: Read-only channel state snapshot.
            context: Optional execution context.
            agent_definition: Optional AgentDefinition for per-invocation overrides.

        Returns:
            Dict with response, usage, and optionally tool_calls.
        """
        from hecate.runtime.agent_execution_port import AgentExecutionPort

        port = AgentExecutionPort(
            self._db,
            pre_hook=self._pre_hook,
            post_hook=self._post_hook,
            context_engine=self._context_engine,
        )
        return await port.agent_execute(agent_id, messages, channel_snapshot, context, agent_definition)

    async def context_assemble(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        session_id: UUID,
        model: str = "gpt-4o",
    ) -> dict:
        """Assemble context with provider-specific shaping (4.11).

        Applies the shaping strategy resolved from the model name so each
        provider receives a conversation shape it accepts (system-message
        consolidation for Claude, orphan tool-message dropping for OpenAI).
        """
        from hecate.runtime.context_shaping import shape_context

        shaped_messages, shaped_tools, strategy = shape_context(messages, tools, model)
        return {
            "messages": shaped_messages,
            "tools": shaped_tools,
            "metadata": {"shaping_strategy": strategy},
        }

    async def evidence_query(
        self,
        session_id: UUID,
        min_importance: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query persisted evidence records for a session (4.8)."""
        from sqlalchemy import select

        from hecate.models.evidence import EvidenceModel

        stmt = select(EvidenceModel).where(EvidenceModel.session_id == session_id)
        if min_importance is not None:
            stmt = stmt.where(EvidenceModel.importance >= min_importance)
        stmt = stmt.order_by(EvidenceModel.created_at.desc()).limit(limit)
        rows = (await self._db.execute(stmt)).scalars().all()
        return [
            {
                "id": str(row.id),
                "session_id": str(row.session_id),
                "tool_name": row.tool_name,
                "tool_arguments": row.tool_arguments,
                "raw_content": row.raw_content,
                "normalized_content": row.normalized_content,
                "is_error": row.is_error,
                "importance": row.importance,
                "source_type": row.source_type,
                "provenance": row.provenance,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


def create_runtime_port(
    db: AsyncSession,
    llm_service: Any,
    tool_registry: Any = None,
    pre_hook: Any = None,
    post_hook: Any = None,
    context_engine: Any = None,
) -> RuntimePort:
    """Create a production RuntimePort adapter.

    Args:
        db: Database session for service lookups.
        llm_service: The LLMService instance for LLM invocations.
        tool_registry: Optional ToolRegistry for tool execution.
        pre_hook: Optional PreLLMHook for input safety checks.
        post_hook: Optional PostLLMHook for output safety checks.
        context_engine: Optional ContextEngine for message selection/compression.

    Returns:
        A concrete RuntimePort wired to production services.
    """
    return _ProductionRuntimePort(
        db=db,
        llm_service=llm_service,
        tool_registry=tool_registry,
        pre_hook=pre_hook,
        post_hook=post_hook,
        context_engine=context_engine,
    )
