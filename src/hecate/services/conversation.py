"""Conversation service orchestrating the complete chat loop.

Handles the full conversation flow:
1. User message → Security check
2. Context assembly (prioritization, phase detection, budget check)
3. Agent lookup → LLM invocation with assembled context
4. Tool calling → Result injection → Evidence capture
5. Response streaming
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

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
    ) -> dict[str, Any] | AsyncGenerator[dict[str, Any], None]:
        """Execute a conversation turn with context engineering.

        Args:
            messages: Conversation history.
            model: LLM model to use.
            tools: Available tools for the agent.
            stream: Whether to stream the response.
            max_iterations: Maximum tool calling iterations.
            session_id: Optional session ID for context tracking.

        Returns:
            Response dict or streaming generator.
        """
        # Generate session ID if not provided
        if session_id is None:
            session_id = str(uuid4())

        # Assemble context before LLM invocation
        session_meta = SessionMeta(
            session_id=session_id,
            agent_id="",  # Will be populated by caller if available
            turn_index=self._turn_index,
            model=model,
        )

        assembled = self.assembler.assemble(
            messages=messages,
            tools=tools,
            session_meta=session_meta,
        )

        # Apply provider-specific shaping
        strategy = get_strategy(model)
        shaped_context = strategy.shape(assembled)
        system_param = strategy.get_system_param(assembled)

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
            )

        return await self._complete_chat(
            messages=shaped_context.messages,
            model=model,
            tools=shaped_context.tools,
            max_iterations=max_iterations,
            session_id=session_id,
            system_param=system_param,
        )

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_iterations: int,
        session_id: str,
        system_param: str | None = None,
    ) -> dict[str, Any]:
        """Execute a non-streaming conversation turn with context engineering."""
        formatted_tools = format_tools_for_llm(tools) if tools else None
        current_messages = list(messages)

        for iteration in range(max_iterations):
            response = await llm_service.chat(
                messages=current_messages,
                model=model,
                tools=formatted_tools,
            )

            if not response.tool_calls:
                return {
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a streaming conversation turn with context engineering."""
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
                # Execute the tool (placeholder - will be wired to actual tool execution)
                result = f"Executed {tc['name']} with args {tc['arguments']}"

                # Capture evidence if tracker is available
                if self._evidence_tracker:
                    await self._evidence_tracker.capture(
                        tool_name=tc["name"],
                        tool_arguments=tc["arguments"],
                        result=result,
                        session_id=UUID(session_id),
                        turn_index=self._turn_index,
                    )

                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "result": result,
                    }
                )
            except Exception as e:
                # Capture error evidence
                if self._evidence_tracker:
                    await self._evidence_tracker.capture(
                        tool_name=tc["name"],
                        tool_arguments=tc["arguments"],
                        result=str(e),
                        session_id=UUID(session_id),
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
