"""Concrete EnginePort adapter for agent execution.

Implements the ``agent_execute`` method on EnginePort by resolving the
AgentModel from the database, building an isolated execution context
(system prompt from persona, agent-specific tools, agent-specific knowledge
bases), and invoking via the unified WorkflowExecutionService.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.guardrail import (
    GuardrailAction,
    NoOpPostLLMHook,
    NoOpPreLLMHook,
    PostLLMHook,
    PreLLMHook,
)
from hecate.engine.ports import EnginePort, SpanContext
from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.tool import ToolModel
from hecate.services.rag.service import knowledge_base_service

logger = logging.getLogger(__name__)


class AgentExecutionPort(EnginePort):
    """EnginePort adapter that handles agent execution via database lookups.

    This adapter implements ``agent_execute`` by:
    1. Loading the AgentModel from the database by ID.
    2. Building an isolated context using the agent's persona, model config,
       tools, and knowledge bases.
    3. Applying guard hooks (PreLLMHook / PostLLMHook).
    4. Calling context_assemble for context engineering.
    5. Invoking the LLM with tools via LLMService.
    6. Returning the response.

    Also implements ``knowledge_query`` by delegating to the KnowledgeBaseService
    for hybrid search (dense + sparse vectors).
    """

    def __init__(
        self,
        db: AsyncSession,
        pre_hook: PreLLMHook | None = None,
        post_hook: PostLLMHook | None = None,
        context_engine: Any | None = None,
    ) -> None:
        self._db = db
        self._pre_hook = pre_hook or NoOpPreLLMHook()
        self._post_hook = post_hook or NoOpPostLLMHook()
        self._context_engine = context_engine

    async def agent_execute(
        self,
        agent_id: UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
        agent_definition: Any | None = None,
    ) -> dict:
        """Execute an agent by ID with full pipeline.

        Loads tools, queries knowledge bases, applies guard hooks,
        calls context_assemble, and invokes the LLM with tools.

        Args:
            agent_id: UUID of the agent to execute.
            messages: Conversation messages from parent graph.
            channel_snapshot: Read-only channel state snapshot.
            context: Optional execution context (node_id, etc.).
            agent_definition: Optional AgentDefinition for per-invocation overrides.

        Returns:
            Dict with ``response``, ``usage``, and optionally ``tool_calls``.

        Raises:
            ValueError: If agent_id does not resolve to a valid agent.
        """
        from hecate.services.llm.service import llm_service
        from hecate.services.skill.loader import SkillLoader

        agent = await self._load_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        model_name = (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )

        # Build system prompt from persona + skills
        persona = agent.persona or "You are a helpful assistant."
        loader = SkillLoader(self._db)
        skills_block = await loader.format_skills(
            agent_id=agent_id,
            workspace_id=agent.workspace_id,
        )
        system_content = f"{persona}\n\n{skills_block}" if skills_block else persona
        system_message = {"role": "system", "content": system_content}
        full_messages = [system_message] + messages

        # Load agent tools from database
        tools = await self._load_agent_tools(agent.tools)

        # Apply AgentDefinition tool filtering (whitelist/blacklist)
        if agent_definition is not None and hasattr(agent_definition, "tools"):
            allowed = agent_definition.tools
            if allowed is not None:
                tools = [t for t in tools if t.get("name", "") in allowed]

        # Query agent's knowledge bases and inject as context
        kb_ids = agent.knowledge_base_ids or []
        if kb_ids:
            query_text = messages[-1].get("content", "") if messages else ""
            kb_chunks = await self.knowledge_query(query_text, [UUID(kb_id) for kb_id in kb_ids])
            if kb_chunks:
                kb_context = "\n\n".join(chunk.get("content", "") for chunk in kb_chunks[:5])
                full_messages.insert(1, {"role": "system", "content": f"Relevant context:\n{kb_context}"})

        # PreLLMHook
        pre_result = await self._pre_hook.on_pre_llm_call(
            messages=full_messages,
            model=model_name,
            tools=tools if tools else None,
        )
        if pre_result.action == GuardrailAction.BLOCK:
            return {
                "response": f"I cannot process this request: {pre_result.reason}",
                "usage": {},
                "model": model_name,
            }
        if (
            pre_result.action == GuardrailAction.SANITIZE
            and pre_result.modified_data
            and "messages" in pre_result.modified_data
        ):
            full_messages = pre_result.modified_data["messages"]

        # Context assembly
        assembled = await self.context_assemble(
            messages=full_messages,
            tools=tools if tools else None,
            session_id=channel_snapshot.get("_session_id", agent_id),
            model=model_name,
        )
        shaped_messages = assembled.get("messages", full_messages)
        shaped_tools = assembled.get("tools", tools if tools else None)

        # LLM invocation
        response = await llm_service.chat(
            messages=shaped_messages,
            model=model_name,
            tools=shaped_tools if shaped_tools else None,
        )

        response_dict: dict[str, Any] = {
            "content": response.content,
            "model": response.model or model_name,
        }

        # PostLLMHook
        post_result = await self._post_hook.on_post_llm_call(
            response=response_dict,
            messages=shaped_messages,
        )
        if post_result.action == GuardrailAction.BLOCK:
            return {
                "response": "I cannot provide that response due to safety policy.",
                "usage": response.usage,
                "model": model_name,
            }
        if (
            post_result.action == GuardrailAction.SANITIZE
            and post_result.modified_data
            and "response" in post_result.modified_data
        ):
            response_dict = post_result.modified_data["response"]

        return {
            "response": response_dict.get("content", response.content),
            "usage": response.usage,
            "model": response.model or model_name,
        }

    async def _load_agent_tools(self, tool_names: list[str]) -> list[dict[str, Any]]:
        """Load tool definitions from the database by name.

        Args:
            tool_names: List of tool names from AgentModel.tools.

        Returns:
            List of tool definition dicts with name, description, parameters.
        """
        if not tool_names:
            return []

        result = await self._db.execute(
            select(ToolModel).where(
                ToolModel.name.in_(tool_names),
                ~ToolModel.deleted,
            )
        )
        tool_models = result.scalars().all()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            }
            for tool in tool_models
        ]

    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        """Query knowledge bases in parallel and return relevant document chunks.

        Looks up the Qdrant collection names for the given kb_ids and performs
        hybrid search (dense + sparse) on each in parallel, aggregating results.

        Args:
            query: The search query string.
            kb_ids: UUIDs of the knowledge bases to search.

        Returns:
            A list of document chunk dicts with content and metadata.
        """
        if not kb_ids:
            return []

        async def _search_one_kb(kb_id: UUID) -> list[dict]:
            """Search a single KB and return chunk dicts. Returns [] on failure."""
            try:
                result = await self._db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == kb_id,
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None:
                    logger.warning(f"Knowledge base {kb_id} not found. Skipping.")
                    return []

                search_results = await knowledge_base_service.search(
                    collection_name=kb.collection_name,
                    query=query,
                    limit=10,
                    mode="hybrid",
                )
                return [
                    {
                        "content": r.content,
                        "metadata": {
                            **r.metadata,
                            "score": r.score,
                            "kb_id": str(kb_id),
                            "kb_name": kb.name,
                        },
                    }
                    for r in search_results
                ]
            except Exception as e:
                logger.error(f"Knowledge search failed for kb {kb_id}: {e}")
                return []

        results = await asyncio.gather(*[_search_one_kb(kb_id) for kb_id in kb_ids])
        all_chunks = [chunk for sublist in results for chunk in sublist]
        all_chunks.sort(key=lambda x: x["metadata"].get("score", 0), reverse=True)
        return all_chunks[:20]

    async def _load_agent(self, agent_id: UUID) -> AgentModel | None:
        """Load an agent from the database by ID.

        Args:
            agent_id: The UUID of the agent to load.

        Returns:
            The AgentModel if found and not deleted, None otherwise.
        """
        result = await self._db.execute(
            select(AgentModel).where(
                AgentModel.id == agent_id,
                ~AgentModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    # Stub implementations for other EnginePort methods.
    # These are required by the ABC but not used for agent execution.

    def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Use LLMService directly for streaming LLM invocation")

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        raise NotImplementedError("Use tool execution service directly")

    async def checkpoint_save(self, state: dict) -> UUID:
        raise NotImplementedError("Use CheckpointStore directly")

    async def checkpoint_load(self, checkpoint_id: UUID) -> dict:
        raise NotImplementedError("Use CheckpointStore directly")

    async def conversation_load(self, session_id: UUID) -> list[dict]:
        raise NotImplementedError("Use conversation API directly")

    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None:
        raise NotImplementedError("Use conversation API directly")

    async def create_span(
        self,
        name: str,
        parent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContext | None:
        """Create an observability span via OTel tracer."""
        try:
            from opentelemetry import trace

            tracer = trace.get_tracer(__name__)
            span = tracer.start_span(name, attributes=attributes)
            ctx = span.get_span_context()
            return SpanContext(
                span_id=format(ctx.span_id, "016x"),
                trace_id=format(ctx.trace_id, "032x"),
                parent_id=parent_id,
            )
        except Exception:
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
        except Exception as exc:
            logger.warning("Failed to end OTel span: %s", exc)
