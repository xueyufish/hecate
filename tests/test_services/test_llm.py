"""Tests for LLM service and tool calling protocol.

Tests cover:
- LLM service initialization
- Tool definition formatting
- Tool call parsing
- Tool result injection
- Error handling
"""

from __future__ import annotations

from hecate.services.llm.tool_calling import (
    create_tool_result_message,
    format_tools_for_llm,
    inject_tool_results,
    parse_tool_calls,
)


def test_format_tools_for_llm() -> None:
    """Test converting Hecate tool definitions to LLM format."""
    tools = [
        {
            "name": "get_weather",
            "description": "Get weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"],
            },
        }
    ]
    result = format_tools_for_llm(tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "get_weather"
    assert "location" in result[0]["function"]["parameters"]["properties"]


def test_format_tools_empty_list() -> None:
    """Test formatting empty tool list."""
    result = format_tools_for_llm([])
    assert result == []


def test_parse_tool_calls() -> None:
    """Test parsing tool calls from LLM response."""
    tool_calls = [
        {
            "id": "call_123",
            "function": {
                "name": "get_weather",
                "arguments": '{"location": "Paris"}',
            },
        }
    ]
    result = parse_tool_calls(tool_calls)
    assert len(result) == 1
    assert result[0]["id"] == "call_123"
    assert result[0]["name"] == "get_weather"
    assert result[0]["arguments"] == {"location": "Paris"}


def test_parse_tool_calls_invalid_json() -> None:
    """Test parsing tool calls with invalid JSON arguments."""
    tool_calls = [
        {
            "id": "call_456",
            "function": {
                "name": "test_tool",
                "arguments": "invalid json",
            },
        }
    ]
    result = parse_tool_calls(tool_calls)
    assert len(result) == 1
    assert result[0]["arguments"] == {}


def test_create_tool_result_message() -> None:
    """Test creating a tool result message."""
    msg = create_tool_result_message("call_123", {"temp": 22})
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_123"
    assert "22" in msg["content"]


def test_create_tool_result_message_error() -> None:
    """Test creating a tool result message for errors."""
    msg = create_tool_result_message("call_123", "Connection failed", is_error=True)
    assert msg["role"] == "tool"
    assert "Error:" in msg["content"]


def test_inject_tool_results() -> None:
    """Test injecting tool results into message history."""
    messages = [
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "I'll check the weather."},
    ]
    tool_calls = [
        {"id": "call_789", "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'}}
    ]
    results = [
        {"tool_call_id": "call_789", "result": {"temp": 20, "condition": "sunny"}}
    ]

    updated = inject_tool_results(messages, tool_calls, results)
    assert len(updated) == 3
    assert updated[1]["role"] == "assistant"
    assert "tool_calls" in updated[1]
    assert updated[2]["role"] == "tool"
    assert updated[2]["tool_call_id"] == "call_789"


def test_inject_tool_results_empty() -> None:
    """Test injecting empty tool results."""
    messages = [{"role": "user", "content": "Hello"}]
    updated = inject_tool_results(messages, [], [])
    assert len(updated) == 1
