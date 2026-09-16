from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from hecate.runtime.guardrail import GuardrailAction, GuardrailResult
from hecate.runtime.workers.llm_worker import LLMWorker


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


class TestResumeValueInjection:
    """1.3.4 — HITL result correction routes ``_resume_value`` into the next LLM call."""

    async def test_string_resume_value_injected_as_user_message(self) -> None:
        """Plain string resume becomes a user-role message."""
        port = _make_port(["ack"])
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={
                "messages": [{"role": "user", "content": "earlier turn"}],
                "_resume_value": "Use 50ms not 100ms — I corrected the timeout",
            },
        )
        # The injected message is the one passed to context_assemble / llm_invoke.
        args, kwargs = port._invoke_tracker.call_args
        sent_messages = kwargs.get("messages") or (args[0] if args else [])
        # The corrected message must be the LAST user-role message — earlier turns
        # are kept verbatim above it.
        user_messages = [m for m in sent_messages if m.get("role") == "user"]
        assert user_messages[-1]["content"] == "Use 50ms not 100ms — I corrected the timeout"
        # And the resume value is cleared so it does not re-inject on the next turn.
        assert result.channel_updates.get("_resume_value") is None

    async def test_dict_with_messages_key_appended(self) -> None:
        port = _make_port(["ok"])
        worker = LLMWorker(port=port)
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={
                "messages": [{"role": "user", "content": "earlier turn"}],
                "_resume_value": {
                    "messages": [
                        {"role": "assistant", "content": "(human rewrite)"},
                        {"role": "user", "content": "Use 50ms not 100ms"},
                    ]
                },
            },
        )
        args, kwargs = port._invoke_tracker.call_args
        sent_messages = kwargs.get("messages") or (args[0] if args else [])
        roles = [m.get("role") for m in sent_messages]
        contents = [m.get("content") for m in sent_messages]
        assert roles[-2:] == ["assistant", "user"]
        assert "(human rewrite)" in contents[-2]
        assert "Use 50ms not 100ms" in contents[-1]

    async def test_resume_value_absent_means_no_injection(self) -> None:
        """No _resume_value → no extra user messages injected."""
        port = _make_port(["ok"])
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={
                "messages": [{"role": "user", "content": "only this"}],
            },
        )
        args, kwargs = port._invoke_tracker.call_args
        sent_messages = kwargs.get("messages") or (args[0] if args else [])
        assert len(sent_messages) == 1
        # No clear when there was no resume value to consume.
        assert "_resume_value" not in result.channel_updates

    async def test_streaming_resume_value_injected(self) -> None:
        """execute_stream also honours _resume_value."""
        port = _make_port(["stream-ack"])
        worker = LLMWorker(port=port)
        events = []
        async for event in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={
                "messages": [{"role": "user", "content": "earlier"}],
                "_resume_value": "corrected value",
            },
        ):
            events.append(event)
        final = next(e for e in events if not isinstance(e, dict))
        assert final.channel_updates.get("_resume_value") is None
        # Verify the LLM call received the corrected message
        args, kwargs = port._invoke_tracker.call_args
        sent_messages = kwargs.get("messages") or (args[0] if args else [])
        user_messages = [m for m in sent_messages if m.get("role") == "user"]
        assert user_messages[-1]["content"] == "corrected value"

    async def test_resume_value_with_pre_llm_block_clears_value(self) -> None:
        """Even when PreLLMHook blocks the call, _resume_value is cleared so it
        does not persist into the next turn."""
        from hecate.runtime.guardrail import PreLLMHook

        class BlockOnceHook(PreLLMHook):
            def __init__(self):
                self.calls = 0

            async def on_pre_llm_call(self, **kwargs):
                self.calls += 1
                return GuardrailResult(action=GuardrailAction.BLOCK, reason="blocked")

        pre_hook = BlockOnceHook()
        port = _make_port(["ignored"])
        worker = LLMWorker(port=port, pre_llm_hook=pre_hook)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={
                "messages": [{"role": "user", "content": "earlier"}],
                "_resume_value": "should be cleared even on block",
            },
        )
        # The blocked refusal message is emitted; resume_value is cleared.
        assert result.channel_updates["messages"][0]["content"].startswith("I cannot process")
        assert result.channel_updates.get("_resume_value") is None


class _RecordingEventStore:
    """Minimal event store recording appended events."""

    def __init__(self) -> None:
        self.events: list = []

    async def append(self, event) -> None:
        self.events.append(event)

    async def get_version(self, session_id) -> int:
        return len(self.events)


class _OverBudgetEngine:
    """ContextEngine that never gets under budget — drives all degradation levels."""

    def select_messages(self, history, budget):
        return list(history)

    def compress(self, messages):
        return list(messages)

    def estimate_tokens(self, messages):
        return 10_000 if messages else 0


class TestTokenBudgetGovernance:
    """Degradation ladder + budget snapshot persistence (4.10 + 4.13 chain)."""

    async def test_under_budget_is_noop(self) -> None:
        from hecate.runtime.context import InMemoryContextEngine
        from hecate.runtime.workers.llm_worker import _run_context_pipeline

        messages = [{"role": "user", "content": "hi"}]
        report = await _run_context_pipeline(
            messages,
            {"max_tokens": 10_000},
            {"context_budget": 10_000, "context_engine": InMemoryContextEngine()},
        )
        assert report.messages == messages
        assert report.levels == []
        assert report.stop_reason is None

    async def test_no_context_engine_is_noop(self) -> None:
        from hecate.runtime.workers.llm_worker import _run_context_pipeline

        messages = [{"role": "user", "content": "hi" * 10_000}]
        report = await _run_context_pipeline(messages, {}, {"context_budget": 10})
        assert report.messages == messages

    async def test_drop_level_selection(self) -> None:
        from hecate.runtime.context import InMemoryContextEngine
        from hecate.runtime.workers.llm_worker import _run_context_pipeline

        messages = [{"role": "user", "content": "x" * 200} for _ in range(20)]
        ctx = {"context_budget": 100, "context_engine": InMemoryContextEngine()}
        report = await _run_context_pipeline(messages, {}, ctx)
        assert len(report.messages) < len(messages)
        assert report.messages == messages[-len(report.messages) :]
        assert "drop" in report.levels

    async def test_over_budget_all_levels_terminate_and_snapshot_event(self) -> None:
        from hecate.runtime.context_processors import STOP_REASON_BUDGET_CAPPED
        from hecate.runtime.workers.llm_worker import _run_context_pipeline

        event_store = _RecordingEventStore()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "newer"},
        ]
        ctx = {
            "context_budget": 50,
            "context_engine": _OverBudgetEngine(),
            "event_store": event_store,
            "session_id": "s1",
            "superstep": 3,
        }
        report = await _run_context_pipeline(messages, {}, ctx, "n1")
        assert report.messages  # survivors exist
        assert report.stop_reason == STOP_REASON_BUDGET_CAPPED
        snapshots = [e for e in event_store.events if e.payload.get("event_name") == "BUDGET_SNAPSHOT"]
        assert len(snapshots) == 1
        payload = snapshots[0].payload
        assert payload["budget"] == 50
        assert "compress" in payload["levels"]
        assert "terminate" in payload["levels"]
        assert payload["tokens_before"] == 10_000
        assert payload["stop_reason"] == STOP_REASON_BUDGET_CAPPED
        assert snapshots[0].session_id == "s1"
        assert snapshots[0].superstep == 3

    async def test_terminate_keeps_system_and_newest_user(self) -> None:
        """When compression drops pinned messages, termination re-inserts them."""
        from hecate.runtime.context_processors import (
            ChainContext,
            ContextUnit,
            HeuristicTokenEstimator,
            TerminationProcessor,
        )

        originals = [
            ContextUnit([{"role": "system", "content": "keep me"}]),
            ContextUnit([{"role": "user", "content": "old"}]),
            ContextUnit([{"role": "user", "content": "new"}]),
            ContextUnit([{"role": "assistant", "content": "answer"}]),
        ]
        # Post-compression projection lost the system and user units.
        units = [originals[3]]
        ctx = ChainContext(
            budget=10,
            estimator=HeuristicTokenEstimator(),
            state={"original_units": list(originals)},
        )
        out, result = await TerminationProcessor().process(units, ctx)
        roles = [m.get("role") for u in out for m in u.messages]
        assert "system" in roles
        assert "user" in roles
        assert result.level == "terminate"


class TestTaskPhaseDetection:
    """Task phase wiring: gate context + span attributes (4.9)."""

    def _capturing_gate(self):
        class _Gate:
            def __init__(self) -> None:
                self.seen: dict | None = None

            def filter_tools(self, tools, context):
                self.seen = context
                return tools

        return _Gate()

    async def test_phase_reaches_tool_gate_context(self) -> None:
        port = _make_port(["ok"])
        worker = LLMWorker(port=port)
        gate = self._capturing_gate()
        worker._tool_gate = gate
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": [{"type": "function", "function": {"name": "t"}}]},
            channel_snapshot={"messages": [{"role": "user", "content": "please verify the result"}]},
        )
        assert gate.seen is not None
        assert gate.seen["task_phase"] == "verify"

    async def test_phase_recorded_on_span(self) -> None:
        port = _make_port(["ok"])
        worker = LLMWorker(port=port)
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "go implement it now"}]},
        )
        attributes = port.create_span.call_args.kwargs["attributes"]
        assert attributes["task_phase"] == "execute"

    async def test_phase_defaults_to_explore(self) -> None:
        port = _make_port(["ok"])
        worker = LLMWorker(port=port)
        gate = self._capturing_gate()
        worker._tool_gate = gate
        await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o", "tools": [{"type": "function", "function": {"name": "t"}}]},
            channel_snapshot={"messages": [{"role": "user", "content": "hello there"}]},
        )
        assert gate.seen["task_phase"] == "explore"
