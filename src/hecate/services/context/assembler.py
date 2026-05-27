"""Context assembler orchestrating all context engineering components.

The main entry point for context engineering - coordinates prioritization,
phase detection, tool filtering, work panel construction, and budget checking
to produce an optimized context for LLM invocations.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from hecate.services.context.budget import BudgetCheck, BudgetManager, DegradationLevel
from hecate.services.context.degradation import DegradationEngine
from hecate.services.context.phase_detector import PhaseDetector
from hecate.services.context.prioritizer import MessagePrioritizer
from hecate.services.context.token_counter import TokenCounter
from hecate.services.context.tool_filter import ToolFilter
from hecate.services.context.types import AssembledContext, SessionMeta
from hecate.services.context.work_panel import WorkPanelBuilder

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

    def assemble(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        session_meta: SessionMeta,
        knowledge: list[dict[str, Any]] | None = None,
    ) -> AssembledContext:
        """Assemble optimized context for an LLM invocation.

        Args:
            messages: Raw conversation messages.
            tools: Available tool definitions.
            session_meta: Session metadata (session_id, model, etc.).
            knowledge: Optional knowledge chunks from RAG.

        Returns:
            AssembledContext with optimized messages, tools, and metadata.
        """
        if not messages:
            return AssembledContext(
                messages=[],
                tools=tools or [],
                knowledge=knowledge or [],
            )

        # Step 1: Assign priorities
        priorities = self.prioritizer.assign_priorities(messages)

        # Step 2: Detect task phase
        phase = self.phase_detector.detect_phase(messages)

        # Step 3: Filter tools by phase
        filtered_tools = self.tool_filter.filter_tools(tools or [], phase)

        # Step 4: Build work panel for long conversations
        if self.work_panel_builder.should_build_panel(messages):
            panel_messages = self.work_panel_builder.build_panel(messages, priorities)
            # Recalculate priorities for the panel
            panel_priorities = self.prioritizer.assign_priorities(panel_messages)
        else:
            panel_messages = messages
            panel_priorities = priorities

        # Step 5: Count tokens
        message_tokens = self.token_counter.count_messages(panel_messages)
        tool_tokens = self.token_counter.count_tool_definitions(filtered_tools)
        total_tokens = message_tokens + tool_tokens

        # Step 6: Check budget and apply degradation if needed
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
                "degradation_level": degradation_level.value,
                "budget_check": {
                    "within_budget": True,  # After degradation
                    "total_tokens": total_tokens,
                },
            },
        )

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
