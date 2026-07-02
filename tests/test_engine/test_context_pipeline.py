from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from hecate.engine.context import InMemoryContextEngine
from hecate.engine.workers.llm_worker import (
    LLMWorker,
    _resolve_budget,
    _truncate_tool_results,
)


def _make_port(tokens: list[str] | None = None) -> MagicMock:
    port = MagicMock()

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    tokens = tokens or ["Hello", " world"]

    invoke_tracker = MagicMock()
    invoke_tracker.tokens = tokens

    async def fake_llm_invoke(*args, **kwargs):
        invoke_tracker.call_args = (args, kwargs)
        for t in tokens:
            yield t

    port.llm_invoke = fake_llm_invoke
    port._invoke_tracker = invoke_tracker
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    return port


class TestTruncateToolResults:
    def test_oversized_tool_result_truncated(self) -> None:
        big_content = "x" * 20000
        messages = [{"role": "tool", "content": big_content}]
        result = _truncate_tool_results(messages, tool_result_limit=500)
        assert len(result[0]["content"]) < len(big_content)
        assert "[... truncated]" in result[0]["content"]

    def test_small_tool_result_unchanged(self) -> None:
        messages = [{"role": "tool", "content": "small result"}]
        result = _truncate_tool_results(messages, tool_result_limit=2000)
        assert result[0]["content"] == "small result"

    def test_non_tool_message_passthrough(self) -> None:
        big_content = "x" * 20000
        messages = [{"role": "user", "content": big_content}]
        result = _truncate_tool_results(messages, tool_result_limit=500)
        assert result[0]["content"] == big_content

    def test_original_messages_not_modified(self) -> None:
        big_content = "x" * 20000
        original = [{"role": "tool", "content": big_content}]
        _truncate_tool_results(original, tool_result_limit=500)
        assert original[0]["content"] == big_content

    def test_empty_list_passthrough(self) -> None:
        assert _truncate_tool_results([], tool_result_limit=500) == []


class TestResolveBudget:
    def test_per_node_priority(self) -> None:
        budget = _resolve_budget(
            {"max_tokens": 16000},
            {"context_budget": 8000},
        )
        assert budget == 16000

    def test_runtime_fallback(self) -> None:
        budget = _resolve_budget(
            {},
            {"context_budget": 12000},
        )
        assert budget == 12000

    def test_default_fallback(self) -> None:
        budget = _resolve_budget({}, None)
        assert budget == 8000

    def test_all_absent(self) -> None:
        budget = _resolve_budget({}, {})
        assert budget == 8000

    def test_invalid_node_budget_ignored(self) -> None:
        budget = _resolve_budget(
            {"max_tokens": -1},
            {"context_budget": 4000},
        )
        assert budget == 4000


class TestContextPipeline:
    async def test_pipeline_filters_when_over_budget(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        ctx_engine = InMemoryContextEngine(max_messages=50, chars_per_token=1)
        big_messages = [{"role": "user", "content": "x" * 200} for _ in range(20)]
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "max_tokens": 100},
            channel_snapshot={"messages": big_messages},
            execution_context={"session_id": "s1", "superstep": 1, "context_engine": ctx_engine},
        )
        assert result.error is None
        shaped_messages = port.context_assemble.call_args.kwargs["messages"]
        assert len(shaped_messages) < len(big_messages)

    async def test_pipeline_without_context_engine_backward_compat(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        messages = [{"role": "user", "content": "Hi"}]
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": messages},
            execution_context={"session_id": "s1", "superstep": 1},
        )
        assert result.error is None
        shaped_messages = port.context_assemble.call_args.kwargs["messages"]
        assert len(shaped_messages) == 1

    async def test_pipeline_non_destructive_channel(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        ctx_engine = InMemoryContextEngine(chars_per_token=1)
        big_messages = [{"role": "user", "content": "x" * 200} for _ in range(20)]
        original_count = len(big_messages)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "max_tokens": 100},
            channel_snapshot={"messages": big_messages},
            execution_context={"session_id": "s1", "superstep": 1, "context_engine": ctx_engine},
        )
        assert result.error is None
        assert len(big_messages) == original_count
        assert result.channel_updates["messages"] == [{"role": "assistant", "content": "OK"}]

    async def test_pipeline_streaming_filters(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        ctx_engine = InMemoryContextEngine(chars_per_token=1)
        big_messages = [{"role": "user", "content": "x" * 200} for _ in range(20)]
        results = []
        async for item in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o", "max_tokens": 100},
            channel_snapshot={"messages": big_messages},
            execution_context={"session_id": "s1", "superstep": 1, "context_engine": ctx_engine},
        ):
            results.append(item)
        worker_result = [r for r in results if hasattr(r, "node_id")]
        assert len(worker_result) == 1
        assert worker_result[0].error is None
        assert worker_result[0].channel_updates["messages"] == [{"role": "assistant", "content": "OK"}]

    async def test_pipeline_streaming_without_context_engine(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        messages = [{"role": "user", "content": "Hi"}]
        results = []
        async for item in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": messages},
            execution_context={"session_id": "s1", "superstep": 1},
        ):
            results.append(item)
        worker_result = [r for r in results if hasattr(r, "node_id")]
        assert len(worker_result) == 1
        assert worker_result[0].error is None

    async def test_tool_result_truncation_in_pipeline(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        ctx_engine = InMemoryContextEngine(chars_per_token=1)
        big_tool_result = "x" * 20000
        messages = [
            {"role": "user", "content": "run tool"},
            {"role": "tool", "content": big_tool_result},
        ]
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tool_result_limit": 500},
            channel_snapshot={"messages": messages},
            execution_context={"session_id": "s1", "superstep": 1, "context_engine": ctx_engine},
        )
        shaped_messages = port.context_assemble.call_args.kwargs["messages"]
        tool_msg = [m for m in shaped_messages if m.get("role") == "tool"]
        if tool_msg:
            assert "[... truncated]" in tool_msg[0]["content"]
