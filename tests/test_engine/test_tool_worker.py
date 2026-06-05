from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from hecate.engine.guardrail import GuardrailAction, GuardrailResult
from hecate.engine.workers.tool_worker import ToolWorker


def _make_port(tool_result: str = "tool output") -> MagicMock:
    port = MagicMock()
    port.tool_execute = AsyncMock(return_value=tool_result)
    return port


class TestToolWorker:
    async def test_no_tool_calls(self) -> None:
        port = _make_port()
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert result.channel_updates["messages"] == []
        port.tool_execute.assert_not_called()

    async def test_tool_call_execution(self) -> None:
        port = _make_port("search results")
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {"query": "test"}}}],
                    }
                ]
            },
        )
        assert len(result.channel_updates["messages"]) == 1
        assert result.channel_updates["messages"][0]["role"] == "tool"
        assert result.channel_updates["messages"][0]["tool_call_id"] == "tc_1"
        assert "search results" in result.channel_updates["messages"][0]["content"]
        port.tool_execute.assert_called_once()

    async def test_multiple_tool_calls(self) -> None:
        port = MagicMock()
        port.tool_execute = AsyncMock(side_effect=["result_1", "result_2"])
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "tc_1", "function": {"name": "tool_a", "arguments": {}}},
                            {"id": "tc_2", "function": {"name": "tool_b", "arguments": {}}},
                        ],
                    }
                ]
            },
        )
        assert len(result.channel_updates["messages"]) == 2
        assert port.tool_execute.call_count == 2

    async def test_tool_execution_error(self) -> None:
        port = _make_port()
        port.tool_execute = AsyncMock(side_effect=RuntimeError("Tool failed"))
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "bad_tool", "arguments": {}}}],
                    }
                ]
            },
        )
        assert result.channel_updates["messages"][0]["is_error"] is True
        assert "Tool failed" in result.channel_updates["messages"][0]["content"]

    async def test_json_string_arguments(self) -> None:
        port = _make_port("parsed")
        worker = ToolWorker(port=port)
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "tc_1", "function": {"name": "search", "arguments": '{"query": "test"}'}}
                        ],
                    }
                ]
            },
        )
        call_args = port.tool_execute.call_args
        assert call_args.kwargs["args"] == {"query": "test"}

    async def test_invalid_json_arguments(self) -> None:
        port = _make_port("fallback")
        worker = ToolWorker(port=port)
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": "not-json"}}],
                    }
                ]
            },
        )
        call_args = port.tool_execute.call_args
        assert call_args.kwargs["args"] == {}

    async def test_pre_hook_blocks(self) -> None:
        port = _make_port()
        pre_hook = MagicMock()
        pre_hook.on_pre_tool_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Dangerous tool")
        )
        worker = ToolWorker(port=port, pre_tool_hook=pre_hook)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "dangerous", "arguments": {}}}],
                    }
                ]
            },
        )
        assert result.channel_updates["messages"][0]["is_error"] is True
        assert "Dangerous tool" in result.channel_updates["messages"][0]["content"]
        port.tool_execute.assert_not_called()

    async def test_post_hook_sanitizes(self) -> None:
        port = _make_port("sensitive data")
        post_hook = MagicMock()
        post_hook.on_post_tool_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="PII detected")
        )
        worker = ToolWorker(port=port, post_tool_hook=post_hook)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {}}}],
                    }
                ]
            },
        )
        assert "Result sanitized" in result.channel_updates["messages"][0]["content"]
        assert "PII detected" in result.channel_updates["messages"][0]["content"]

    async def test_post_hook_allows(self) -> None:
        port = _make_port("clean result")
        post_hook = MagicMock()
        post_hook.on_post_tool_call = AsyncMock(return_value=GuardrailResult(action=GuardrailAction.ALLOW))
        worker = ToolWorker(port=port, post_tool_hook=post_hook)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {}}}],
                    }
                ]
            },
        )
        assert "clean result" in result.channel_updates["messages"][0]["content"]
        assert result.channel_updates["messages"][0].get("is_error") is None
