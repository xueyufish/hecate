"""Tests for GuardrailAction.SANITIZE and GuardrailResult.modified_data."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.guardrail import (
    GuardrailAction,
    GuardrailResult,
    NoOpPostLLMHook,
    NoOpPostToolHook,
    NoOpPreLLMHook,
    NoOpPreToolHook,
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
)
from hecate.engine.ports import EnginePort
from hecate.engine.types import WorkerResult
from hecate.engine.workers.llm_worker import LLMWorker
from hecate.engine.workers.tool_worker import ToolWorker

# -- Helper hooks that return SANITIZE --


class SanitizePreLLMHook(PreLLMHook):
    async def on_pre_llm_call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
    ) -> GuardrailResult:
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={"messages": [{"role": "user", "content": "sanitized_input"}]},
        )


class SanitizePostLLMHook(PostLLMHook):
    async def on_post_llm_call(
        self,
        response: dict,
        messages: list[dict],
    ) -> GuardrailResult:
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={"response": {"content": "sanitized_output", "model": response.get("model", "")}},
        )


class SanitizePostToolHook(PostToolHook):
    async def on_post_tool_call(
        self,
        name: str,
        result: Any,
        context: dict | None,
    ) -> GuardrailResult:
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={"result": "masked_result"},
        )


class SanitizeNoDataHook(PreLLMHook):
    async def on_pre_llm_call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
    ) -> GuardrailResult:
        return GuardrailResult(action=GuardrailAction.SANITIZE)


# -- Stub EnginePort for testing --


class StubPort(EnginePort):
    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        yield "test response"

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        return f"tool_result_{name}"

    async def llm_invoke_batch(self, messages: list[dict], config: dict) -> str:
        return "test response"

    async def knowledge_query(self, query: str, kb_ids: list[uuid.UUID]) -> list[dict]:
        return []

    async def checkpoint_save(self, state: dict) -> uuid.UUID:
        return uuid.uuid4()

    async def checkpoint_load(self, checkpoint_id: uuid.UUID) -> dict:
        return {}

    async def conversation_load(self, session_id: uuid.UUID) -> list[dict]:
        return []

    async def conversation_save(self, session_id: uuid.UUID, messages: list[dict]) -> None:
        pass

    async def create_span(
        self, name: str, parent_id: str | None = None, attributes: dict[str, Any] | None = None
    ) -> Any:
        return None

    async def end_span(
        self, span_id: str, output_data: dict[str, Any] | None = None, usage: dict[str, int] | None = None
    ) -> None:
        pass


class TestGuardrailAction:
    """Tests for the three-member GuardrailAction enum."""

    def test_has_three_members(self) -> None:
        assert len(GuardrailAction) == 3

    def test_allow_value(self) -> None:
        assert GuardrailAction.ALLOW == "allow"

    def test_block_value(self) -> None:
        assert GuardrailAction.BLOCK == "block"

    def test_sanitize_value(self) -> None:
        assert GuardrailAction.SANITIZE == "sanitize"

    def test_string_comparison_allow(self) -> None:
        assert GuardrailAction.ALLOW == "allow"

    def test_string_comparison_sanitize(self) -> None:
        assert GuardrailAction.SANITIZE == "sanitize"


class TestGuardrailResult:
    """Tests for GuardrailResult with modified_data field."""

    def test_default_allow_no_modified_data(self) -> None:
        result = GuardrailResult()
        assert result.action == GuardrailAction.ALLOW
        assert result.reason == ""
        assert result.modified_data is None

    def test_block_with_reason(self) -> None:
        result = GuardrailResult(
            action=GuardrailAction.BLOCK,
            reason="Prompt injection detected",
        )
        assert result.action == GuardrailAction.BLOCK
        assert result.reason == "Prompt injection detected"
        assert result.modified_data is None

    def test_sanitize_with_modified_data(self) -> None:
        data = {"messages": [{"role": "user", "content": "sanitized"}]}
        result = GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data=data,
        )
        assert result.action == GuardrailAction.SANITIZE
        assert result.modified_data == data

    def test_sanitize_with_none_modified_data(self) -> None:
        result = GuardrailResult(action=GuardrailAction.SANITIZE)
        assert result.action == GuardrailAction.SANITIZE
        assert result.modified_data is None


class TestNoOpHooksModifiedData:
    """Tests that NoOp hooks return None for modified_data."""

    async def test_noop_pre_llm_hook_no_modified_data(self) -> None:
        hook = NoOpPreLLMHook()
        result = await hook.on_pre_llm_call([], "gpt-4o", None)
        assert result.action == GuardrailAction.ALLOW
        assert result.modified_data is None

    async def test_noop_post_llm_hook_no_modified_data(self) -> None:
        hook = NoOpPostLLMHook()
        result = await hook.on_post_llm_call({}, [])
        assert result.action == GuardrailAction.ALLOW
        assert result.modified_data is None

    async def test_noop_pre_tool_hook_no_modified_data(self) -> None:
        hook = NoOpPreToolHook()
        result = await hook.on_pre_tool_call("test", {}, None)
        assert result.action == GuardrailAction.ALLOW
        assert result.modified_data is None

    async def test_noop_post_tool_hook_no_modified_data(self) -> None:
        hook = NoOpPostToolHook()
        result = await hook.on_post_tool_call("test", "result", None)
        assert result.action == GuardrailAction.ALLOW
        assert result.modified_data is None


# -- Worker SANITIZE tests --


class TestLLMWorkerSanitize:
    async def test_sanitize_from_pre_llm_hook(self) -> None:
        worker = LLMWorker(port=StubPort(), pre_llm_hook=SanitizePreLLMHook())
        result = await worker.execute(
            node_id="test_node",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "original"}]},
        )
        assert result.node_id == "test_node"
        assert result.channel_updates["messages"][0]["content"] == "test response"

    async def test_sanitize_from_post_llm_hook(self) -> None:
        worker = LLMWorker(port=StubPort(), post_llm_hook=SanitizePostLLMHook())
        result = await worker.execute(
            node_id="test_node",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert result.channel_updates["messages"][0]["content"] == "sanitized_output"

    async def test_sanitize_without_modified_data_treats_as_allow(self) -> None:
        worker = LLMWorker(port=StubPort(), pre_llm_hook=SanitizeNoDataHook())
        result = await worker.execute(
            node_id="test_node",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "original"}]},
        )
        assert result.channel_updates["messages"][0]["content"] == "test response"

    async def test_stream_sanitize_from_pre_llm_hook(self) -> None:
        worker = LLMWorker(port=StubPort(), pre_llm_hook=SanitizePreLLMHook())
        chunks: list[dict | WorkerResult] = []
        async for chunk in worker.execute_stream(
            node_id="test_node",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "original"}]},
        ):
            chunks.append(chunk)
        final = chunks[-1]
        assert isinstance(final, WorkerResult)
        assert final.channel_updates["messages"][0]["content"] == "test response"

    async def test_stream_sanitize_from_post_llm_hook(self) -> None:
        worker = LLMWorker(port=StubPort(), post_llm_hook=SanitizePostLLMHook())
        chunks: list[dict | WorkerResult] = []
        async for chunk in worker.execute_stream(
            node_id="test_node",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "hello"}]},
        ):
            chunks.append(chunk)
        final = chunks[-1]
        assert isinstance(final, WorkerResult)
        assert final.channel_updates["messages"][0]["content"] == "sanitized_output"


class TestToolWorkerSanitize:
    async def test_sanitize_from_post_tool_hook(self) -> None:
        worker = ToolWorker(port=StubPort(), post_tool_hook=SanitizePostToolHook())
        result = await worker.execute(
            node_id="test_node",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "name": "search", "arguments": {}}]},
                ],
            },
        )
        tool_msg = result.channel_updates["messages"][0]
        assert tool_msg["content"] == "masked_result"
