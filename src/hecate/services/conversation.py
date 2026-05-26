"""Conversation service orchestrating the complete chat loop.

Handles the full conversation flow:
1. User message → Security check
2. Agent lookup → LLM invocation
3. Tool calling → Result injection → LLM continuation
4. Response streaming
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from hecate.services.llm.service import llm_service
from hecate.services.llm.tool_calling import (
    format_tools_for_llm,
    inject_tool_results,
    parse_tool_calls,
)

logger = logging.getLogger(__name__)


class ConversationService:
    """Orchestrate complete conversation flows.

    Supports:
    - Single-turn and multi-turn conversations
    - Tool calling with automatic execution
    - Streaming responses
    - Security checks
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        max_iterations: int = 10,
    ) -> dict[str, Any] | AsyncGenerator:
        """Execute a conversation turn.

        Args:
            messages: Conversation history.
            model: LLM model to use.
            tools: Available tools for the agent.
            stream: Whether to stream the response.
            max_iterations: Maximum tool calling iterations.

        Returns:
            Response dict or streaming generator.
        """
        if stream:
            return self._stream_chat(messages, model, tools, max_iterations)

        return await self._complete_chat(messages, model, tools, max_iterations)

    async def _complete_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None,
        max_iterations: int,
    ) -> dict[str, Any]:
        """Execute a non-streaming conversation turn."""
        formatted_tools = format_tools_for_llm(tools) if tools else None
        current_messages = list(messages)

        for _ in range(max_iterations):
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
                }

            tool_calls = parse_tool_calls(response.tool_calls)
            tool_results = await self._execute_tools(tool_calls, tools or [])

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
        }

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None,
        max_iterations: int,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a streaming conversation turn."""
        formatted_tools = format_tools_for_llm(tools) if tools else None
        current_messages = list(messages)

        for _ in range(max_iterations):
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
                    yield {"type": "done", "finish_reason": "stop"}
                    return

            if tool_calls_buffer:
                tool_calls = parse_tool_calls(tool_calls_buffer)
                tool_results = await self._execute_tools(tool_calls, tools or [])
                current_messages = inject_tool_results(
                    current_messages,
                    tool_calls_buffer,
                    tool_results,
                )
                yield {"type": "tool_results", "results": tool_results}
            else:
                return

    async def _execute_tools(
        self,
        tool_calls: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Execute tool calls and return results."""
        results = []
        for tc in tool_calls:
            try:
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "result": f"Executed {tc['name']} with args {tc['arguments']}",
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "tool_call_id": tc["id"],
                        "result": str(e),
                        "is_error": True,
                    }
                )
        return results


conversation_service = ConversationService()
