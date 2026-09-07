from __future__ import annotations

from hecate.runtime.context_shaping import (
    AnthropicShapingStrategy,
    DefaultShapingStrategy,
    OpenAIShapingStrategy,
    resolve_shaping_strategy,
    shape_context,
)


def _sys(text: str) -> dict:
    return {"role": "system", "content": text}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str, tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": text}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


def _tool(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class TestAnthropicShaping:
    def test_consolidates_system_messages(self) -> None:
        messages = [_sys("be brief"), _user("hi"), _sys("also be kind")]
        shaped = AnthropicShapingStrategy().shape_messages(messages)
        system_parts = [m for m in shaped if m["role"] == "system"]
        assert len(system_parts) == 1
        assert "be brief" in system_parts[0]["content"]
        assert "also be kind" in system_parts[0]["content"]
        assert shaped[1] == _user("hi")
        assert len(shaped) == 2

    def test_drops_empty_content_messages(self) -> None:
        messages = [_sys("rules"), _user(""), {"role": "assistant", "content": ""}, _user("hello")]
        shaped = AnthropicShapingStrategy().shape_messages(messages)
        roles = [m["role"] for m in shaped]
        assert roles == ["system", "user"]

    def test_keeps_tool_messages_with_empty_string_content(self) -> None:
        messages = [_user("go"), _assistant("", tool_calls=[{"id": "t1"}]), _tool("t1", "")]
        shaped = AnthropicShapingStrategy().shape_messages(messages)
        assert len(shaped) == 3

    def test_non_destructive(self) -> None:
        messages = [_sys("a"), _user(""), _user("hi")]
        original = [dict(m) for m in messages]
        AnthropicShapingStrategy().shape_messages(messages)
        assert messages == original


class TestOpenAIShaping:
    def test_drops_orphan_tool_messages(self) -> None:
        messages = [
            _user("go"),
            _tool("t-orphan", "dangling"),
            _assistant("", tool_calls=[{"id": "t1"}]),
            _tool("t1", "result"),
        ]
        shaped = OpenAIShapingStrategy().shape_messages(messages)
        assert [m["role"] for m in shaped] == ["user", "assistant", "tool"]

    def test_keeps_tool_answer_within_conversation(self) -> None:
        messages = [
            _user("go"),
            _assistant("", tool_calls=[{"id": "t1"}, {"id": "t2"}]),
            _tool("t1"),
            _user("unrelated interjection"),
            _tool("t2"),
        ]
        shaped = OpenAIShapingStrategy().shape_messages(messages)
        # t2 is dropped: the user interjection consumed the pending window.
        assert [m["role"] for m in shaped] == ["user", "assistant", "tool", "user"]


class TestDefaultShaping:
    def test_drops_empty_keeps_order(self) -> None:
        messages = [_user(""), _user("real"), _sys("s")]
        shaped = DefaultShapingStrategy().shape_messages(messages)
        assert shaped == [_user("real"), _sys("s")]


class TestResolveStrategy:
    def test_claude_models_route_to_anthropic(self) -> None:
        assert isinstance(resolve_shaping_strategy("claude-sonnet-4"), AnthropicShapingStrategy)
        assert isinstance(resolve_shaping_strategy("anthropic/claude-3-opus"), AnthropicShapingStrategy)

    def test_openai_compatible_models_route_to_openai(self) -> None:
        assert isinstance(resolve_shaping_strategy("gpt-4o"), OpenAIShapingStrategy)
        assert isinstance(resolve_shaping_strategy("openai/gpt-4o"), OpenAIShapingStrategy)
        assert isinstance(resolve_shaping_strategy("qwen-max"), OpenAIShapingStrategy)
        assert isinstance(resolve_shaping_strategy("deepseek-chat"), OpenAIShapingStrategy)

    def test_tools_pass_through(self) -> None:
        tools = [{"type": "function", "function": {"name": "t"}}]
        _, shaped_tools, _ = shape_context([_user("hi")], tools, "gpt-4o")
        assert shaped_tools == tools
