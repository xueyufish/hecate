"""Tool calling protocol implementation.

Handles the tool calling workflow:
1. Convert Hecate tool definitions to LLM function format
2. Execute tool calls from LLM responses
3. Inject tool results back into message history
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_tools_for_llm(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Hecate tool definitions to OpenAI function calling format.

    Args:
        tools: List of Hecate tool definitions with name, description, parameters.

    Returns:
        List of tool definitions in OpenAI function format.
    """
    formatted = []
    for tool in tools:
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            }
        )
    return formatted


def parse_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse tool calls from LLM response.

    Args:
        tool_calls: Raw tool calls from LLM response.

    Returns:
        List of parsed tool calls with id, name, and arguments.
    """
    parsed = []
    for tc in tool_calls:
        if hasattr(tc, "model_dump"):
            tc = tc.model_dump()

        function = tc.get("function", {})
        arguments = function.get("arguments", "{}")

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        parsed.append(
            {
                "id": tc.get("id", ""),
                "name": function.get("name", ""),
                "arguments": arguments,
            }
        )
    return parsed


def create_tool_result_message(
    tool_call_id: str,
    result: Any,
    is_error: bool = False,
) -> dict[str, Any]:
    """Create a tool result message for injection into conversation.

    Args:
        tool_call_id: The ID of the tool call this result responds to.
        result: The tool execution result.
        is_error: Whether the result is an error.

    Returns:
        dict: A tool role message with the result content.
    """
    content = f"Error: {result}" if is_error else json.dumps(result) if not isinstance(result, str) else result

    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


def inject_tool_results(
    messages: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inject tool results into message history.

    Args:
        messages: Current message history.
        tool_calls: The tool calls from the assistant message.
        results: The tool execution results.

    Returns:
        list: Updated messages with tool results appended.
    """
    updated = list(messages)

    if tool_calls:
        last_msg = messages[-1] if messages else {}
        if last_msg.get("role") == "assistant" and "tool_calls" not in last_msg:
            updated[-1] = {
                "role": "assistant",
                "content": last_msg.get("content"),
                "tool_calls": tool_calls,
            }

    for result in results:
        updated.append(
            create_tool_result_message(
                tool_call_id=result["tool_call_id"],
                result=result["result"],
                is_error=result.get("is_error", False),
            )
        )

    return updated
