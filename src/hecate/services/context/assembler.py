"""Context assembler orchestrating all context engineering components.

The main entry point for context engineering - coordinates prioritization,
phase detection, tool filtering, work panel construction, memory injection,
and budget checking to produce an optimized context for LLM invocations.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from hecate.models.memory import MemoryBlockReadSchema, MemoryReadSchema
from hecate.services.context.budget import BudgetCheck, BudgetManager, DegradationLevel
from hecate.services.context.degradation import DegradationEngine
from hecate.services.context.phase_detector import PhaseDetector
from hecate.services.context.prioritizer import MessagePrioritizer
from hecate.services.context.token_counter import TokenCounter
from hecate.services.context.tool_filter import ToolFilter
from hecate.services.context.types import AssembledContext, SessionMeta
from hecate.services.context.work_panel import WorkPanelBuilder
from hecate.services.harness.constraint_generator import ConstraintRule
from hecate.services.harness.constraint_injector import ConstraintInjector
from hecate.services.memory.compression import CompressionPipeline

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Orchestrates context engineering before LLM invocations.

    Coordinates multiple context engineering components:

    1. Prioritize messages (critical/high/medium/low)
    2. Detect task phase (explore/converge/execute/verify)
    3. Filter tools by phase relevance
    4. Build work panel for long conversations
    5. Check budget and apply degradation if needed
    6. Apply provider-specific shaping (via separate ProviderStrategy)

    Usage:
        assembler = ContextAssembler(budget_manager)
        context = assembler.assemble(messages, tools, session_meta)
        # Pass context.messages and context.tools to LLM service
    """

    def __init__(
        self,
        budget_manager: BudgetManager,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize the context assembler.

        Args:
            budget_manager: BudgetManager for token budget governance.
            token_counter: Optional TokenCounter (auto-created if None).
        """
        self.budget_manager = budget_manager
        self.token_counter = token_counter or TokenCounter()
        self.prioritizer = MessagePrioritizer()
        self.phase_detector = PhaseDetector()
        self.tool_filter = ToolFilter()
        self.work_panel_builder = WorkPanelBuilder()
        self.degradation_engine = DegradationEngine(self.token_counter)
        self.compression_pipeline = CompressionPipeline(self.token_counter)
        self.constraint_injector = ConstraintInjector()

    def assemble(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        session_meta: SessionMeta,
        knowledge: list[dict[str, Any]] | None = None,
        memory_blocks: list[MemoryBlockReadSchema] | None = None,
        user_memories: list[MemoryReadSchema] | None = None,
        constraints: list[ConstraintRule] | None = None,
        suggestion_mode: str | None = None,
    ) -> AssembledContext:
        """Assemble optimized context for an LLM invocation.

        Args:
            messages: Raw conversation messages.
            tools: Available tool definitions.
            session_meta: Session metadata (session_id, model, etc.).
            knowledge: Optional knowledge chunks from RAG.
            memory_blocks: Optional L1 memory blocks to inject.
            user_memories: Optional L3 user memories to inject.
            constraints: Optional constraint rules from harness.
            suggestion_mode: Optional suggestion mode ("opening" or "followup").
                When "opening", builds a system prompt with agent metadata for
                generating opening remarks. When "followup", builds a system
                prompt with the last 2 turns for generating follow-up questions.
                When None, proceeds with standard assembly.

        Returns:
            AssembledContext with optimized messages, tools, and metadata.
        """
        if not messages:
            return AssembledContext(
                messages=[],
                tools=tools or [],
                knowledge=knowledge or [],
            )

        # Handle suggestion modes — return lightweight context for suggestion generation
        if suggestion_mode == "opening":
            return self._build_opening_suggestion_context(session_meta, tools)
        if suggestion_mode == "followup":
            return self._build_followup_suggestion_context(messages, session_meta, tools)

        # Step 1: Inject knowledge chunks with citation mapping
        citation_map: dict[int, dict[str, Any]] = {}
        if knowledge:
            messages, citation_map = self._inject_knowledge(messages, knowledge)

        # Step 2: Inject L1 memory blocks
        if memory_blocks:
            messages = self._inject_memory_blocks(messages, memory_blocks)

        # Step 2: Inject L3 user memories
        if user_memories:
            messages = self._inject_user_memories(messages, user_memories)

        # Step 2.5: Inject constraint rules from harness
        if constraints:
            messages = self.constraint_injector.inject_constraints(messages, constraints)

        # Step 3: Assign priorities
        priorities = self.prioritizer.assign_priorities(messages)

        # Step 4: Detect task phase
        phase = self.phase_detector.detect_phase(messages)

        # Step 5: Filter tools by phase
        filtered_tools = self.tool_filter.filter_tools(tools or [], phase)

        # Step 6: Build work panel for long conversations
        if self.work_panel_builder.should_build_panel(messages):
            panel_messages = self.work_panel_builder.build_panel(messages, priorities)
            # Recalculate priorities for the panel
            panel_priorities = self.prioritizer.assign_priorities(panel_messages)
        else:
            panel_messages = messages
            panel_priorities = priorities

        # Step 7: Count tokens
        message_tokens = self.token_counter.count_messages(panel_messages)
        tool_tokens = self.token_counter.count_tool_definitions(filtered_tools)
        total_tokens = message_tokens + tool_tokens

        # Step 8: Check budget and apply degradation if needed
        session_id = UUID(session_meta.session_id) if session_meta.session_id else None
        final_messages = panel_messages
        final_priorities = panel_priorities
        degradation_level = DegradationLevel.NONE

        if session_id:
            budget_check = self.budget_manager.check_budget(
                session_id=session_id,
                messages=panel_messages,
                model=session_meta.model,
                tools=filtered_tools,
            )

            if not budget_check.within_budget:
                # Apply degradation
                final_messages, degradation_level = self._apply_degradation(
                    messages=panel_messages,
                    priorities=panel_priorities,
                    budget_check=budget_check,
                    target_tokens=budget_check.budget,
                )
                # Recalculate tokens after degradation
                total_tokens = self.token_counter.count_messages(final_messages) + tool_tokens
                # Recalculate priorities
                final_priorities = self.prioritizer.assign_priorities(final_messages)

                # Record degradation event
                self.budget_manager.record_degradation(session_id, degradation_level)

        # Build final context
        return AssembledContext(
            messages=final_messages,
            tools=filtered_tools,
            knowledge=knowledge or [],
            phase=phase,
            total_tokens=total_tokens,
            priorities=final_priorities,
            metadata={
                "original_message_count": len(messages),
                "filtered_tool_count": len(filtered_tools),
                "memory_blocks_count": len(memory_blocks) if memory_blocks else 0,
                "user_memories_count": len(user_memories) if user_memories else 0,
                "degradation_level": degradation_level.value,
                "citation_map": citation_map,
                "budget_check": {
                    "within_budget": True,  # After degradation
                    "total_tokens": total_tokens,
                },
            },
        )

    def _inject_memory_blocks(
        self,
        messages: list[dict[str, Any]],
        blocks: list[MemoryBlockReadSchema],
    ) -> list[dict[str, Any]]:
        """Inject memory blocks into message context.

        Args:
            messages: Original messages.
            blocks: Memory blocks to inject.

        Returns:
            Messages with blocks injected.
        """
        block_messages = []
        for block in blocks:
            if block.content:
                block_messages.append(
                    {
                        "role": "system",
                        "content": f"[{block.label}]: {block.content}",
                    }
                )

        if not block_messages:
            return messages

        # Insert after existing system messages
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        return messages[:insert_idx] + block_messages + messages[insert_idx:]

    def _inject_user_memories(
        self,
        messages: list[dict[str, Any]],
        memories: list[MemoryReadSchema],
    ) -> list[dict[str, Any]]:
        """Inject user memories into message context.

        Args:
            messages: Original messages.
            memories: User memories to inject.

        Returns:
            Messages with memories injected as system message.
        """
        if not memories:
            return messages

        # Create memory context
        memory_lines = []
        for mem in memories[:5]:  # Limit to top 5 memories
            memory_lines.append(f"- {mem.content}")

        memory_context = "[User memories]:\n" + "\n".join(memory_lines)
        memory_message = {"role": "system", "content": memory_context}

        # Insert after existing system messages
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        return messages[:insert_idx] + [memory_message] + messages[insert_idx:]

    def _inject_knowledge(
        self,
        messages: list[dict[str, Any]],
        knowledge: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        if not knowledge:
            return messages, {}

        lines: list[str] = ["The following reference documents are available:"]
        citation_map: dict[int, dict[str, Any]] = {}
        max_content_length = 500

        for idx, chunk in enumerate(knowledge, start=1):
            content = chunk.get("content", "")
            metadata = chunk.get("metadata", {})

            content_snippet = content[:max_content_length] + "..." if len(content) > max_content_length else content

            citation_map[idx] = {
                "chunk_id": str(chunk.get("id", "")),
                "kb_id": metadata.get("kb_id", ""),
                "kb_name": metadata.get("kb_name", ""),
                "document_name": metadata.get("source_file", "unknown"),
                "score": metadata.get("score", 0.0),
                "content_snippet": content_snippet[:150],
            }

            source = metadata.get("source_file", "unknown")
            lines.append(f'[{idx}] "{content_snippet}" (Source: {source})')

        knowledge_text = "\n".join(lines)
        knowledge_message = {"role": "system", "content": knowledge_text}

        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        return messages[:insert_idx] + [knowledge_message] + messages[insert_idx:], citation_map

    def _apply_degradation(
        self,
        messages: list[dict[str, Any]],
        priorities: list[str],
        budget_check: BudgetCheck,
        target_tokens: int,
    ) -> tuple[list[dict[str, Any]], DegradationLevel]:
        """Apply degradation strategies to fit within budget.

        Args:
            messages: Messages to degrade.
            priorities: Priority for each message.
            budget_check: Budget check result.
            target_tokens: Target token count.

        Returns:
            Tuple of (degraded messages, degradation level applied).
        """
        # Level 1: Drop low-priority messages
        dropped = self.degradation_engine.drop_low_priority(messages, priorities, target_tokens)
        dropped_tokens = self.token_counter.count_messages(dropped)
        if dropped_tokens <= target_tokens:
            logger.info(f"Level 1 (DROP) degradation sufficient: {budget_check.total_tokens} → {dropped_tokens}")
            return dropped, DegradationLevel.DROP

        # Level 2: Compress medium-priority messages
        compressed = self.degradation_engine.compress_medium_priority(
            dropped, self.prioritizer.assign_priorities(dropped), target_tokens
        )
        compressed_tokens = self.token_counter.count_messages(compressed)
        if compressed_tokens <= target_tokens:
            logger.info(f"Level 2 (COMPRESS) degradation sufficient: {dropped_tokens} → {compressed_tokens}")
            return compressed, DegradationLevel.COMPRESS

        # Level 3: Emergency summary
        emergency = self.degradation_engine.emergency_summary(messages, target_tokens)
        emergency_tokens = self.token_counter.count_messages(emergency)
        logger.warning(f"Level 3 (EMERGENCY) degradation applied: {compressed_tokens} → {emergency_tokens}")
        return emergency, DegradationLevel.EMERGENCY

    def _build_opening_suggestion_context(
        self,
        session_meta: SessionMeta,
        tools: list[dict[str, Any]] | None,
    ) -> AssembledContext:
        persona_section = f"\nPersona: {session_meta.agent_persona}" if session_meta.agent_persona else ""
        system_content = (
            f"You are {session_meta.agent_name},{persona_section}\n\n"
            "Generate 3 concise suggested questions that a user might ask to start a conversation with this agent. "
            'Return ONLY a JSON array of strings, e.g. ["question1", "question2", "question3"].'
        )
        messages = [{"role": "system", "content": system_content}]
        return AssembledContext(
            messages=messages,
            tools=[],
            metadata={"suggestion_mode": "opening"},
        )

    def _build_followup_suggestion_context(
        self,
        messages: list[dict[str, Any]],
        session_meta: SessionMeta,
        tools: list[dict[str, Any]] | None,
    ) -> AssembledContext:
        history_lines: list[str] = []
        user_assistant_turns = [m for m in messages if m.get("role") in ("user", "assistant")]
        for turn in user_assistant_turns[-4:]:
            role = turn.get("role", "unknown").capitalize()
            content = turn.get("content", "")
            history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines) if history_lines else "(No conversation history yet)"
        system_content = (
            f"You are {session_meta.agent_name}.\n\n"
            f"Recent conversation:\n{history_text}\n\n"
            "Based on this conversation, generate 3 concise follow-up questions the user might want to ask next. "
            'Return ONLY a JSON array of strings, e.g. ["question1", "question2", "question3"].'
        )
        context_messages = [{"role": "system", "content": system_content}]
        return AssembledContext(
            messages=context_messages,
            tools=[],
            metadata={"suggestion_mode": "followup"},
        )
