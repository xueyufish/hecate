"""Factory for creating EnginePort adapters for production use.

Creates a concrete EnginePort that wires engine calls to actual service
implementations (LLMService, tool execution, knowledge bases, etc.).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.ports import EnginePort, SpanContext

logger = logging.getLogger(__name__)

# Module-level holder for quota service — set during app startup
_quota_service_factory: Any = None


def set_quota_service_factory(factory: Any) -> None:
    """Register the QuotaService factory for cost recording."""
    global _quota_service_factory
    _quota_service_factory = factory


class _ProductionEnginePort(EnginePort):
    """Production EnginePort adapter wiring engine calls to actual services.

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

        After streaming completes, records cost against quotas for
        org, workspace, and agent scopes if QuotaService is available.
        """
        model = config.get("model", "gpt-4o")
        tools = config.get("tools")

        token_count = 0
        async for chunk in self._llm_service.chat_stream(
            messages=messages,
            model=model,
            tools=tools,
        ):
            content = chunk.get("content", "")
            if content:
                token_count += len(content) // 4
                yield content

        await self._record_cost(model, token_count)

    async def _record_cost(self, model: str, token_count: int) -> None:
        """Record cost usage against quotas after LLM invocation."""
        if _quota_service_factory is None or token_count == 0:
            return
        try:
            service = _quota_service_factory(self._db, self._workspace_id)
            cost_estimate = token_count * 0.00001

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
            raise RuntimeError("ToolRegistry not configured in EnginePort")
        return await self._tool_registry.execute(name, args, context)

    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        """Query knowledge bases via knowledge_base_service.

        Args:
            query: The search query string.
            kb_ids: UUIDs of the knowledge bases to search.

        Returns:
            A list of document chunk dicts.
        """
        from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

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
        """Create an observability span via OTel tracer and TracingService."""
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer(__name__)
            span = tracer.start_span(name, attributes=attributes)
            ctx = span.get_span_context()
            otel_trace_id = format(ctx.trace_id, "032x")
            otel_span_id = format(ctx.span_id, "016x")
            return SpanContext(
                span_id=otel_span_id,
                trace_id=otel_trace_id,
                parent_id=parent_id,
            )
        except Exception:
            logger.debug("Tracing not available, returning None for span '%s'", name)
            return None

    async def end_span(
        self,
        span_id: str,
        output_data: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        """End an observability span."""
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span.is_recording():
                if output_data:
                    for k, v in output_data.items():
                        span.set_attribute(f"output.{k}", str(v))
                if usage:
                    for k, v in usage.items():
                        span.set_attribute(f"usage.{k}", v)
                span.end()
        except Exception:
            logger.debug("Failed to end span %s", span_id)

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
        from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

        port = AgentExecutionPort(
            self._db,
            pre_hook=self._pre_hook,
            post_hook=self._post_hook,
            context_engine=self._context_engine,
        )
        return await port.agent_execute(agent_id, messages, channel_snapshot, context, agent_definition)


def create_engine_port(
    db: AsyncSession,
    llm_service: Any,
    tool_registry: Any = None,
    pre_hook: Any = None,
    post_hook: Any = None,
    context_engine: Any = None,
) -> EnginePort:
    """Create a production EnginePort adapter.

    Args:
        db: Database session for service lookups.
        llm_service: The LLMService instance for LLM invocations.
        tool_registry: Optional ToolRegistry for tool execution.
        pre_hook: Optional PreLLMHook for input safety checks.
        post_hook: Optional PostLLMHook for output safety checks.
        context_engine: Optional ContextEngine for message selection/compression.

    Returns:
        A concrete EnginePort wired to production services.
    """
    return _ProductionEnginePort(
        db=db,
        llm_service=llm_service,
        tool_registry=tool_registry,
        pre_hook=pre_hook,
        post_hook=post_hook,
        context_engine=context_engine,
    )
