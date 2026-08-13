from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from hecate.engine.guardrail import GuardrailAction, GuardrailResult
from hecate.engine.workers.llm_worker import LLMWorker


def _make_port(tokens: list[str] | None = None) -> MagicMock:
    port = MagicMock()

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    tokens = tokens or ["Hello", " world"]

    invoke_tracker = SimpleNamespace(tokens=tokens, call_args=None, structured_call_args=None)

    async def fake_llm_invoke(*args, **kwargs):
        invoke_tracker.call_args = (args, kwargs)
        for t in tokens:
            yield t

    async def fake_llm_invoke_structured(*args, **kwargs):
        invoke_tracker.structured_call_args = (args, kwargs)
        for t in tokens:
            yield {"content": t, "tool_calls": None}
        yield {"content": None, "tool_calls": None}

    port.llm_invoke = fake_llm_invoke
    port.llm_invoke_structured = fake_llm_invoke_structured
    port._invoke_tracker = invoke_tracker
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    return port


class TestLLMWorker:
    async def test_basic_invocation(self) -> None:
        port = _make_port(["Hello", " ", "world"])
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert result.error is None
        assert result.channel_updates["messages"][0]["content"] == "Hello world"
        assert result.channel_updates["messages"][0]["role"] == "assistant"

    async def test_custom_model(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        await worker.execute(
            node_id="llm",
            node_config={"model": "claude-3"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        _, kwargs = port._invoke_tracker.call_args
        assert kwargs["config"]["model"] == "claude-3"

    async def test_context_assembly_called(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={
                "messages": [{"role": "user", "content": "Hi"}],
                "_session_id": "sess-1",
            },
        )
        port.context_assemble.assert_called_once()

    async def test_error_handling(self) -> None:
        port = _make_port()

        async def failing_invoke(*args, **kwargs):
            raise RuntimeError("LLM API error")
            yield  # make it a generator

        port.llm_invoke = failing_invoke
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert result.error is not None
        assert "LLM API error" in str(result.error)

    async def test_pre_hook_blocks(self) -> None:
        port = _make_port(["blocked"])
        pre_hook = MagicMock()
        pre_hook.on_pre_llm_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Unsafe input")
        )
        worker = LLMWorker(port=port, pre_llm_hook=pre_hook)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "hack"}]},
        )
        assert "cannot process" in result.channel_updates["messages"][0]["content"].lower()
        assert "Unsafe input" in result.channel_updates["messages"][0]["content"]

    async def test_post_hook_blocks(self) -> None:
        port = _make_port(["toxic response"])
        post_hook = MagicMock()
        post_hook.on_post_llm_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Toxic content")
        )
        worker = LLMWorker(port=port, post_llm_hook=post_hook)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert "cannot provide" in result.channel_updates["messages"][0]["content"].lower()

    async def test_both_hooks_allow(self) -> None:
        port = _make_port(["safe response"])
        pre_hook = MagicMock()
        pre_hook.on_pre_llm_call = AsyncMock(return_value=GuardrailResult(action=GuardrailAction.ALLOW))
        post_hook = MagicMock()
        post_hook.on_post_llm_call = AsyncMock(return_value=GuardrailResult(action=GuardrailAction.ALLOW))
        worker = LLMWorker(port=port, pre_llm_hook=pre_hook, post_llm_hook=post_hook)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert result.error is None
        assert result.channel_updates["messages"][0]["content"] == "safe response"

    async def test_streaming_yields_tokens(self) -> None:
        port = _make_port(["Hello", " ", "world"])
        worker = LLMWorker(port=port)
        events = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        ):
            events.append(event)

        token_events = [e for e in events if isinstance(e, dict) and "content" in e]
        final_events = [e for e in events if not isinstance(e, dict)]

        assert len(token_events) == 3
        assert token_events[0]["content"] == "Hello"
        assert token_events[1]["content"] == " "
        assert token_events[2]["content"] == "world"
        assert len(final_events) == 1
        assert final_events[0].channel_updates["messages"][0]["content"] == "Hello world"

    async def test_streaming_pre_hook_blocks(self) -> None:
        port = _make_port(["blocked"])
        pre_hook = MagicMock()
        pre_hook.on_pre_llm_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Blocked")
        )
        worker = LLMWorker(port=port, pre_llm_hook=pre_hook)
        events = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        ):
            events.append(event)
        assert len(events) == 1
        assert "cannot process" in events[0].channel_updates["messages"][0]["content"].lower()

    async def test_streaming_post_hook_blocks(self) -> None:
        port = _make_port(["toxic"])
        post_hook = MagicMock()
        post_hook.on_post_llm_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Toxic")
        )
        worker = LLMWorker(port=port, post_llm_hook=post_hook)
        events = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        ):
            events.append(event)
        token_events = [e for e in events if isinstance(e, dict) and "content" in e]
        final_events = [e for e in events if not isinstance(e, dict)]
        assert len(token_events) == 1
        assert token_events[0]["content"] == "toxic"
        assert len(final_events) == 1
        assert "cannot provide" in final_events[0].channel_updates["messages"][0]["content"].lower()

    async def test_tool_gating_filters_before_llm_call(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        tools: list[dict] = [
            {"name": "admin_tool", "available_when": "role == 'admin'"},
            {"name": "public_tool"},
        ]
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": tools},
            channel_snapshot={
                "messages": [{"role": "user", "content": "Hi"}],
                "role": "user",
            },
            execution_context={},
        )
        _, kwargs = port._invoke_tracker.structured_call_args
        passed_tools = kwargs["config"]["tools"]
        assert len(passed_tools) == 1
        assert passed_tools[0]["name"] == "public_tool"

    async def test_tool_gating_no_available_when_passthrough(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        tools: list[dict] = [
            {"name": "tool_a"},
            {"name": "tool_b"},
        ]
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": tools},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
            execution_context={},
        )
        _, kwargs = port._invoke_tracker.structured_call_args
        assert len(kwargs["config"]["tools"]) == 2

    async def test_tool_gating_pre_hook_sees_filtered_tools(self) -> None:
        port = _make_port(["OK"])
        pre_hook = MagicMock()
        pre_hook.on_pre_llm_call = AsyncMock(return_value=GuardrailResult(action=GuardrailAction.ALLOW))
        worker = LLMWorker(port=port, pre_llm_hook=pre_hook)
        tools: list[dict] = [
            {"name": "admin_tool", "available_when": "role == 'admin'"},
        ]
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": tools},
            channel_snapshot={
                "messages": [{"role": "user", "content": "Hi"}],
                "role": "user",
            },
            execution_context={},
        )
        called_tools = pre_hook.on_pre_llm_call.call_args[1].get("tools", [])
        assert called_tools == [] or called_tools is None

    async def test_tool_gating_streaming(self) -> None:
        port = _make_port(["stream"])
        worker = LLMWorker(port=port)
        tools: list[dict] = [
            {"name": "blocked", "available_when": "x == 1"},
            {"name": "allowed"},
        ]
        events: list = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": tools},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
            execution_context={},
        ):
            events.append(event)
        _, kwargs = port._invoke_tracker.structured_call_args
        assert len(kwargs["config"]["tools"]) == 1
        assert kwargs["config"]["tools"][0]["name"] == "allowed"

    async def test_tool_gating_with_expression_context(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        tools: list[dict] = [
            {"name": "matching", "available_when": "user_role == 'admin'"},
            {"name": "non_matching", "available_when": "user_role == 'guest'"},
        ]
        channel = {
            "messages": [{"role": "user", "content": "Hi"}],
        }
        ctx = {"user_role": "admin", "session_id": "sess-1", "superstep": 1}
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": tools},
            channel_snapshot=channel,
            execution_context=ctx,
        )
        _, kwargs = port._invoke_tracker.structured_call_args
        passed = kwargs["config"]["tools"]
        assert len(passed) == 1
        assert passed[0]["name"] == "matching"

    async def test_tool_gating_missing_keys_fail_closed(self) -> None:
        port = _make_port(["OK"])
        worker = LLMWorker(port=port)
        tools: list[dict] = [
            {"name": "dependent", "available_when": "nonexistent > 5"},
        ]
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": tools},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
            execution_context={},
        )
        _, kwargs = port._invoke_tracker.call_args
        assert kwargs["config"]["tools"] == []


class TestLLMWorkerToolCallDetection:
    """Tests for structured tool_calls detection in LLMWorker."""

    def _make_structured_port(self, chunks: list[dict[str, object]]) -> MagicMock:
        """Create a port mock whose llm_invoke_structured yields the given chunks."""
        from unittest.mock import MagicMock

        port = MagicMock()

        async def fake_context_assemble(*args, **kwargs):
            return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

        port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

        async def fake_structured(*args, **kwargs):
            for c in chunks:
                yield c

        port.llm_invoke = MagicMock()  # should NOT be called when tools are present
        port.llm_invoke_structured = fake_structured
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        return port

    async def test_non_streaming_detects_tool_calls(self) -> None:
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"query":"weather"}'},
            },
        ]
        port = self._make_structured_port(
            [
                {"content": "Let me ", "tool_calls": None},
                {"content": "search.", "tool_calls": None},
                {"content": None, "tool_calls": tool_calls},
            ]
        )
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": [{"type": "function"}]},
            channel_snapshot={"messages": [{"role": "user", "content": "Weather?"}]},
        )
        assert result.error is None
        assert result.channel_updates.get("_has_tool_call") is True
        assistant = result.channel_updates["messages"][0]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == "Let me search."
        assert assistant["tool_calls"] == tool_calls

    async def test_non_streaming_no_tool_calls(self) -> None:
        port = self._make_structured_port(
            [
                {"content": "Hi", "tool_calls": None},
                {"content": " there", "tool_calls": None},
                {"content": None, "tool_calls": None},
            ]
        )
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": [{"type": "function"}]},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert result.error is None
        assert result.channel_updates["_has_tool_call"] is False
        assistant = result.channel_updates["messages"][0]
        assert "tool_calls" not in assistant

    async def test_streaming_detects_tool_calls(self) -> None:
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calc", "arguments": '{"expr":"2+2"}'},
            },
        ]
        port = self._make_structured_port(
            [
                {"content": "Calc", "tool_calls": None},
                {"content": "ing...", "tool_calls": None},
                {"content": None, "tool_calls": tool_calls},
            ]
        )
        worker = LLMWorker(port=port)
        events: list = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": [{"type": "function"}]},
            channel_snapshot={"messages": [{"role": "user", "content": "Calc 2+2"}]},
        ):
            events.append(event)
        token_events = [e for e in events if isinstance(e, dict) and "content" in e]
        final_events = [e for e in events if not isinstance(e, dict)]
        assert token_events == [{"content": "Calc"}, {"content": "ing..."}]
        assert len(final_events) == 1
        final = final_events[0]
        assert final.channel_updates.get("_has_tool_call") is True
        assert final.channel_updates["messages"][0]["tool_calls"] == tool_calls

    async def test_streaming_no_tool_calls(self) -> None:
        port = self._make_structured_port(
            [
                {"content": "Just", "tool_calls": None},
                {"content": " text", "tool_calls": None},
                {"content": None, "tool_calls": None},
            ]
        )
        worker = LLMWorker(port=port)
        events: list = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": [{"type": "function"}]},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        ):
            events.append(event)
        final_events = [e for e in events if not isinstance(e, dict)]
        assert len(final_events) == 1
        assert final_events[0].channel_updates["_has_tool_call"] is False

    async def test_non_streaming_without_tools_uses_llm_invoke(self) -> None:
        """Regression: no tools → llm_invoke path, no tool_call detection."""
        port = _make_port(["plain", " ", "text"])
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert result.error is None
        assert result.channel_updates["_has_tool_call"] is False
        assert result.channel_updates["messages"][0]["content"] == "plain text"
        assert port._invoke_tracker.call_args is not None
        assert port._invoke_tracker.structured_call_args is None
