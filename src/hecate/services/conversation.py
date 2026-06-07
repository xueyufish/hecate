"""Conversation service orchestrating the complete chat loop.

Handles the full conversation flow:
1. User message → Security check
2. Context assembly (prioritization, phase detection, budget check)
3. Memory loading (L1 blocks, L2 compression, L3 retrieval)
4. Agent lookup → LLM invocation with assembled context
5. Tool calling → Result injection → Evidence capture
6. L3 fact extraction (async, non-blocking)
7. Response streaming
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.memory import MemoryBlockReadSchema, MemoryCreateSchema, MemoryReadSchema
from hecate.services.context.assembler import ContextAssembler
from hecate.services.context.budget import BudgetManager
from hecate.services.context.evidence_tracker import EvidenceTracker
from hecate.services.context.provider_shaping import get_strategy
from hecate.services.context.token_counter import TokenCounter
from hecate.services.context.types import SessionMeta
from hecate.services.llm.service import llm_service
from hecate.services.llm.tool_calling import (
    format_tools_for_llm,
    inject_tool_results,
    parse_tool_calls,
)
from hecate.services.memory.compression import CompressionPipeline
from hecate.services.memory.user_memory import UserMemoryService
from hecate.services.memory.working_memory import WorkingMemoryService
from hecate.services.rag.service import knowledge_base_service
from hecate.services.rag.types import Citation
from hecate.services.suggestions.service import SuggestionService

logger = logging.getLogger(__name__)


class ConversationService:
    """Orchestrate complete conversation flows with Context Engineering.

    Supports:
    - Single-turn and multi-turn conversations
    - Tool calling with automatic execution
    - Streaming responses
    - Context assembly with budget governance
    - Evidence tracking for tool results
    - Provider-specific context shaping
    - L1 working memory block injection
    - L2 conversation compression (token threshold)
    - L3 user memory retrieval and fact extraction
    - Memory tool registration (update_memory_block, search_user_memory)
    """

    def __init__(
        self,
        budget_manager: BudgetManager | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize the conversation service.

        Args:
            budget_manager: Optional budget manager for token governance.
            token_counter: Optional token counter for budget calculations.
        """
        self.budget_manager = budget_manager or BudgetManager()
        self.token_counter = token_counter or TokenCounter()
        self.assembler = ContextAssembler(self.budget_manager, self.token_counter)
        self.compression_pipeline = CompressionPipeline(self.token_counter)
        self.suggestion_service = SuggestionService(llm_service)
        self._evidence_tracker: EvidenceTracker | None = None
        self._turn_index: int = 0

    def set_evidence_tracker(self, tracker: EvidenceTracker) -> None:
        """Set the evidence tracker for tool result capture.

        Args:
            tracker: EvidenceTracker instance with database session.
        """
        self._evidence_tracker = tracker

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_iterations: int = 10,
        session_id: str | None = None,
        db: AsyncSession | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        compression_threshold: int = 4000,
        kb_ids: list[uuid.UUID] | None = None,
        generate_opening: bool = False,
        generate_suggestions: bool = False,
        agent_persona: str | None = None,
        enable_suggestions: bool = True,
    ) -> dict[str, Any] | AsyncGenerator[dict[str, Any], None]:
        """Execute a conversation turn with context engineering.

        Args:
            messages: Conversation history.
            model: LLM model to use.
            tools: Available tools for the agent.
            stream: Whether to stream the response.
            max_iterations: Maximum tool calling iterations.
            session_id: Optional session ID for context tracking.
            db: Optional database session for memory operations.
            agent_id: Optional agent ID for L1 memory block loading.
            user_id: Optional user ID for L3 memory retrieval.
            compression_threshold: Token count that triggers L2 compression.
            generate_opening: Whether to generate opening remarks with suggestions.
            generate_suggestions: Whether to generate follow-up suggestions.
            agent_persona: Agent persona description for suggestion context.
            enable_suggestions: Whether the agent has suggestions enabled.

        Returns:
            Response dict or streaming generator.
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        # L2 compression: compress history if token count exceeds threshold
        if self.compression_pipeline:
            token_count = self.token_counter.count_messages(messages)
            if token_count > compression_threshold:
                result = self.compression_pipeline.compress(messages, token_threshold=compression_threshold)
                messages = result.messages

        # L1 working memory: load agent memory blocks
        memory_blocks: list[MemoryBlockReadSchema] | None = None
        if db and agent_id:
            wm_service = WorkingMemoryService(db)
            memory_blocks = await wm_service.list_blocks(uuid.UUID(agent_id))

        # L3 user memory: retrieve relevant memories
        user_memories: list[MemoryReadSchema] | None = None
        if db and user_id:
            um_service = UserMemoryService(db)
            query_text = messages[-1].get("content", "") if messages else ""
            user_memories = await um_service.retrieve_memories(
                query=query_text,
                scope={"user_id": user_id},
                top_k=5,
            )

        # Knowledge retrieval for RAG citations
        knowledge_chunks: list[dict[str, Any]] = []
        if db and kb_ids:
            knowledge_chunks = await self._retrieve_knowledge(db, kb_ids, messages)

        # Register memory tools conditionally
        if tools is not None:
            memory_tools = self._build_memory_tools(db, agent_id, user_id)
            if memory_tools:
                tools = list(tools) + memory_tools

        # Assemble context before LLM invocation
        session_meta = SessionMeta(
            session_id=session_id,
            agent_id=agent_id or "",
            turn_index=self._turn_index,
            model=model,
        )

        assembled = self.assembler.assemble(
            messages=messages,
            tools=tools,
            session_meta=session_meta,
            knowledge=knowledge_chunks,
            memory_blocks=memory_blocks,
            user_memories=user_memories,
        )

        # Apply provider-specific shaping
        strategy = get_strategy(model)
        shaped_context = strategy.shape(assembled)
        system_param = strategy.get_system_param(assembled)

        # Build citations from citation_map
        citations: list[Citation] = []
        citation_map = assembled.metadata.get("citation_map", {})
        for pos, meta in citation_map.items():
            try:
                citations.append(
                    Citation(
                        position=pos,
                        kb_id=uuid.UUID(meta["kb_id"]) if meta.get("kb_id") else uuid.UUID(int=0),
                        kb_name=meta.get("kb_name", ""),
                        document_name=meta.get("document_name", "unknown"),
                        chunk_id=meta.get("chunk_id", ""),
                        score=meta.get("score", 0.0),
                        content_snippet=meta.get("content_snippet", ""),
                    )
                )
            except (ValueError, KeyError):
                logger.warning(f"Failed to build citation for position {pos}")

        # Increment turn index for next call
        self._turn_index += 1

        if stream:
            return self._stream_chat(
                messages=shaped_context.messages,
                model=model,
                tools=shaped_context.tools,
                max_iterations=max_iterations,
                session_id=session_id,
                system_param=system_param,
                db=db,
                user_id=user_id,
                original_messages=messages,
                citations=citations,
                generate_opening=generate_opening,
                generate_suggestions=generate_suggestions,
                agent_persona=agent_persona,
                enable_suggestions=enable_suggestions,
            )

        return await self._complete_chat(
            messages=shaped_context.messages,
            model=model,
            tools=shaped_context.tools,
            max_iterations=max_iterations,
            session_id=session_id,
            system_param=system_param,
            db=db,
            user_id=user_id,
            original_messages=messages,
            citations=citations,
            generate_opening=generate_opening,
            generate_suggestions=generate_suggestions,
            agent_persona=agent_persona,
            enable_suggestions=enable_suggestions,
        )

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_iterations: int,
        session_id: str,
        system_param: str | None = None,
        db: AsyncSession | None = None,
        user_id: str | None = None,
        original_messages: list[dict[str, Any]] | None = None,
        citations: list[Citation] | None = None,
        generate_opening: bool = False,
        generate_suggestions: bool = False,
        agent_persona: str | None = None,
        enable_suggestions: bool = True,
    ) -> dict[str, Any]:
        """Execute a non-streaming conversation turn with context engineering."""
        if generate_opening and self._turn_index <= 1:
            opening = await self._generate_opening_remarks(agent_persona)
            return {
                "content": opening["content"],
                "model": model,
                "usage": {},
                "finish_reason": "stop",
                "suggested_questions": opening["suggested_questions"],
                "context_metadata": {
                    "session_id": session_id,
                    "turn_index": self._turn_index - 1,
                    "iteration": 0,
                },
            }

        formatted_tools = format_tools_for_llm(tools) if tools else None
        current_messages = list(messages)

        for iteration in range(max_iterations):
            response = await llm_service.chat(
                messages=current_messages,
                model=model,
                tools=formatted_tools,
            )

            if not response.tool_calls:
                await self._extract_facts_async(db, user_id, original_messages or messages)

                result = {
                    "content": response.content,
                    "model": response.model,
                    "usage": response.usage,
                    "finish_reason": response.finish_reason,
                    "context_metadata": {
                        "session_id": session_id,
                        "turn_index": self._turn_index - 1,
                        "iteration": iteration,
                    },
                }
                if citations:
                    result["citations"] = [c.model_dump() for c in citations]

                if generate_suggestions and enable_suggestions and response.content:
                    suggestions = await self._generate_followup_suggestions(
                        agent_persona,
                        original_messages or messages,
                        response.content,
                    )
                    result["suggested_questions"] = suggestions

                return result

            # Execute tools and capture evidence
            tool_calls = parse_tool_calls(response.tool_calls)
            tool_results = await self._execute_tools_with_evidence(tool_calls, tools or [], session_id)

            current_messages = inject_tool_results(
                current_messages,
                response.tool_calls,
                tool_results,
            )

        return {
            "content": "Maximum tool calling iterations reached.",
            "model": model,
            "usage": {},
            "finish_reason": "max_iterations",
            "context_metadata": {
                "session_id": session_id,
                "turn_index": self._turn_index - 1,
                "iteration": max_iterations,
            },
        }

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_iterations: int,
        session_id: str,
        system_param: str | None = None,
        db: AsyncSession | None = None,
        user_id: str | None = None,
        original_messages: list[dict[str, Any]] | None = None,
        citations: list[Citation] | None = None,
        generate_opening: bool = False,
        generate_suggestions: bool = False,
        agent_persona: str | None = None,
        enable_suggestions: bool = True,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a streaming conversation turn with context engineering."""
        if generate_opening and self._turn_index <= 1:
            opening = await self._generate_opening_remarks(agent_persona)
            yield {"type": "content", "content": opening["content"]}
            yield {"type": "suggestions", "questions": opening["suggested_questions"]}
            yield {
                "type": "done",
                "finish_reason": "stop",
                "context_metadata": {
                    "session_id": session_id,
                    "turn_index": self._turn_index - 1,
                    "iteration": 0,
                },
            }
            return

        formatted_tools = format_tools_for_llm(tools) if tools else None
        current_messages = list(messages)

        for iteration in range(max_iterations):
            tool_calls_buffer = []
            content_buffer = []

            async for chunk in llm_service.chat_stream(
                messages=current_messages,
                model=model,
                tools=formatted_tools,
            ):
                if chunk.get("content"):
                    content_buffer.append(chunk["content"])
                    yield {"type": "content", "content": chunk["content"]}

                if chunk.get("tool_calls"):
                    tool_calls_buffer.extend(chunk["tool_calls"])

                if chunk.get("finish_reason") == "stop":
                    await self._extract_facts_async(db, user_id, original_messages or messages)

                    if citations:
                        yield {
                            "type": "citations",
                            "citations": [c.model_dump() for c in citations],
                        }

                    if generate_suggestions and enable_suggestions:
                        full_content = "".join(content_buffer)
                        if full_content:
                            suggestions = await self._generate_followup_suggestions(
                                agent_persona,
                                original_messages or messages,
                                full_content,
                            )
                            yield {"type": "suggestions", "questions": suggestions}

                    yield {
                        "type": "done",
                        "finish_reason": "stop",
                        "context_metadata": {
                            "session_id": session_id,
                            "turn_index": self._turn_index - 1,
                            "iteration": iteration,
                        },
                    }
                    return

            if tool_calls_buffer:
                # Execute tools and capture evidence
                tool_calls = parse_tool_calls(tool_calls_buffer)
                tool_results = await self._execute_tools_with_evidence(tool_calls, tools or [], session_id)
                current_messages = inject_tool_results(
                    current_messages,
                    tool_calls_buffer,
                    tool_results,
                )
                yield {"type": "tool_results", "results": tool_results}
            else:
                return

    async def _generate_opening_remarks(
        self,
        agent_persona: str | None,
    ) -> dict[str, Any]:
        """Generate opening greeting and suggested questions.

        Args:
            agent_persona: Agent persona description for context.

        Returns:
            Dict with 'content' (greeting text) and 'suggested_questions' list.
        """
        result = await self.suggestion_service.generate_opening(
            agent_persona=agent_persona,
            agent_capabilities=[],
        )
        greeting = agent_persona or "Hello! How can I help you today?"
        return {
            "content": greeting,
            "suggested_questions": result.questions,
        }

    async def _generate_followup_suggestions(
        self,
        agent_persona: str | None,
        messages: list[dict[str, Any]],
        current_response: str,
    ) -> list[str]:
        """Generate follow-up question suggestions after a response.

        Args:
            agent_persona: Agent persona description for context.
            messages: Full conversation message history.
            current_response: The agent's latest response content.

        Returns:
            List of suggested question strings.
        """
        recent = messages[-4:] if len(messages) > 4 else messages
        history_parts: list[str] = []
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                history_parts.append(f"{role}: {content}")
        history_str = "\n".join(history_parts)

        result = await self.suggestion_service.generate_suggestions(
            agent_persona=agent_persona,
            conversation_history=history_str,
            current_response=current_response,
        )
        return result.questions

    async def _extract_facts_async(
        self,
        db: AsyncSession | None,
        user_id: str | None,
        messages: list[dict[str, Any]],
    ) -> None:
        """Extract facts from conversation for L3 memory (non-blocking).

        Args:
            db: Database session for memory storage.
            user_id: User ID to scope extracted facts.
            messages: Conversation messages to extract from.
        """
        if not db or not user_id:
            return

        try:
            um_service = UserMemoryService(db)
            facts = await um_service.extract_facts(messages)
            for fact in facts:
                await um_service.store_memory(
                    MemoryCreateSchema(
                        content=fact,
                        scope={"user_id": user_id},
                        memory_type="semantic",
                    )
                )
        except Exception:
            logger.warning("Failed to extract user memories", exc_info=True)

    def _build_memory_tools(
        self,
        db: AsyncSession | None,
        agent_id: str | None,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        """Build memory tool definitions based on available memory services.

        Args:
            db: Database session (required for memory tools).
            agent_id: Agent ID (required for update_memory_block).
            user_id: User ID (required for search_user_memory).

        Returns:
            List of tool definitions for memory operations.
        """
        if not db:
            return []

        memory_tools: list[dict[str, Any]] = []

        if agent_id:
            memory_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "update_memory_block",
                        "description": (
                            "Update a working memory block for the current agent. "
                            "Use this to store important context that should persist across turns."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "The label of the memory block to update",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The new content for the memory block",
                                },
                            },
                            "required": ["label", "content"],
                        },
                    },
                }
            )

        if user_id:
            memory_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "search_user_memory",
                        "description": (
                            "Search the user's persistent memory for relevant facts, "
                            "preferences, or history. Use this to recall information about the user."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query to find relevant user memories",
                                },
                            },
                            "required": ["query"],
                        },
                    },
                }
            )

        return memory_tools

    async def _retrieve_knowledge(
        self,
        db: AsyncSession,
        kb_ids: list[uuid.UUID],
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Retrieve knowledge chunks from specified knowledge bases in parallel.

        Args:
            db: Database session for KB model lookup.
            kb_ids: UUIDs of knowledge bases to search.
            messages: Conversation messages to extract query from.

        Returns:
            List of knowledge chunk dicts sorted by score, max 5.
        """
        query_text = messages[-1].get("content", "") if messages else ""
        if not query_text:
            return []

        async def _search_one_kb(kb_id: uuid.UUID) -> list[dict[str, Any]]:
            """Search a single KB and return chunk dicts. Returns [] on failure."""
            try:
                result = await db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == kb_id,
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None:
                    logger.warning(f"Knowledge base {kb_id} not found, skipping")
                    return []

                search_results = await knowledge_base_service.search(
                    collection_name=kb.collection_name,
                    query=query_text,
                    limit=10,
                    mode="hybrid",
                )
                return [
                    {
                        "id": r.id,
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
                logger.error(f"Knowledge retrieval failed for kb {kb_id}: {e}")
                return []

        results = await asyncio.gather(*[_search_one_kb(kb_id) for kb_id in kb_ids])
        all_chunks = [chunk for sublist in results for chunk in sublist]
        all_chunks.sort(key=lambda x: x["metadata"].get("score", 0), reverse=True)
        return all_chunks[:5]

    async def _execute_tools_with_evidence(
        self,
        tool_calls: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        session_id: str,
    ) -> list[dict[str, Any]]:
        """Execute tool calls and capture evidence for results.

        Args:
            tool_calls: Parsed tool calls with name, arguments, id.
            tools: Available tool definitions.
            session_id: Current session ID for evidence tracking.

        Returns:
            List of tool results for injection into messages.
        """
        results = []
        for tc in tool_calls:
            try:
                result = f"Executed {tc['name']} with args {tc['arguments']}"

                if self._evidence_tracker:
                    await self._evidence_tracker.capture(
                        tool_name=tc["name"],
                        tool_arguments=tc["arguments"],
                        result=result,
                        session_id=uuid.UUID(session_id),
                        turn_index=self._turn_index,
                    )

                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "result": result,
                    }
                )
            except Exception as e:
                if self._evidence_tracker:
                    await self._evidence_tracker.capture(
                        tool_name=tc["name"],
                        tool_arguments=tc["arguments"],
                        result=str(e),
                        session_id=uuid.UUID(session_id),
                        turn_index=self._turn_index,
                        is_error=True,
                    )

                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "result": str(e),
                        "is_error": True,
                    }
                )
        return results


conversation_service = ConversationService()
