from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from hecate.engine.guardrail import GuardrailAction, GuardrailResult
from hecate.engine.workers.llm_worker import LLMWorker


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
