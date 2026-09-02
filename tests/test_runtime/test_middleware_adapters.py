"""Tests for ``engine.middleware_adapters`` — legacy hook ABC ↔ chain bridge.

The four legacy guardrail hook ABCs (``PreLLMHook`` / ``PostLLMHook`` /
``PreToolHook`` / ``PostToolHook``) must be usable as chain stages without
rewrites. Each adapter preserves the hook's matcher filter and forwards the
result into the chain kernel's decision vocabulary.
"""

from __future__ import annotations

import pytest

from hecate.runtime.guardrail import (
    GuardrailAction,
    GuardrailResult,
    NoOpPreLLMHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.runtime.middleware import Chain, Phase
from hecate.runtime.middleware_adapters import (
    adapt_post_tool_hook,
    adapt_pre_llm_hook,
    adapt_pre_tool_hook,
)


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_pre_llm_hook_adapter_no_op_passes_through():
    """A NoOp pre-LLM hook acts as a passthrough stage."""
    chain = Chain(phase=Phase.AGENT_REQUEST)
    chain.set_handler(lambda data: _async_return("terminal"))

    adapter = adapt_pre_llm_hook("noop-pre", NoOpPreLLMHook())
    chain.add_stage("noop-pre", adapter)

    decision, result = await chain.run({"messages": ["hello"], "model": "m", "tools": None})
    assert decision.action == GuardrailAction.ALLOW
    assert decision.stage_id == "noop-pre"
    assert result == "terminal"


@pytest.mark.asyncio
async def test_pre_tool_hook_adapter_matcher_filters_invocation():
    """A pre-tool hook with a matcher that doesn't match the tool name MUST be a
    passthrough (does not invoke ``on_pre_tool_call``)."""
    invocations: list[str] = []

    class _Recorder(PreToolHook):
        matcher = "bash"

        async def on_pre_tool_call(self, name, arguments, context):
            invocations.append(name)
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="should-not-reach")

    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    chain.set_handler(lambda data: _async_return("exec"))

    adapter = adapt_pre_tool_hook("pretool", _Recorder())
    chain.add_stage("pretool", adapter)

    # ``bash`` matches the recorder — expect BLOCK
    decision, _ = await chain.run({"name": "bash", "arguments": {}, "context": None})
    assert decision.action == GuardrailAction.BLOCK
    assert invocations == ["bash"]

    # ``read_file`` does NOT match — expect ALLOW passthrough
    decision, result = await chain.run({"name": "read_file", "arguments": {}, "context": None})
    assert decision.action == GuardrailAction.ALLOW
    assert result == "exec"
    assert invocations == ["bash"]  # recorder NOT called


@pytest.mark.asyncio
async def test_pre_llm_hook_adapter_sanitizes_messages():
    """A pre-LLM hook that returns SANITIZE with modified_data is passed through
    to subsequent stages (kernel applies the contract check)."""

    class _Sanitizer(PreLLMHook):
        async def on_pre_llm_call(self, messages, model, tools):
            new_messages = ["REDACTED"]
            return GuardrailResult(
                action=GuardrailAction.SANITIZE,
                modified_data={"messages": new_messages},
            )

    chain = Chain(phase=Phase.AGENT_REQUEST)
    seen: list[object] = []

    async def terminal(data):
        seen.append(data)
        return "done"

    adapter = adapt_pre_llm_hook("sanitize", _Sanitizer())
    chain.add_stage("sanitize", adapter)
    chain.set_handler(terminal)

    decision, result = await chain.run({"messages": ["original"], "model": "m", "tools": None})
    assert decision.action == GuardrailAction.SANITIZE
    # The adapter forwards modified_data as the replacement payload for the
    # subsequent stages — kernel semantics replace data wholesale on SANITIZE.
    assert seen == [{"messages": ["REDACTED"]}]
    assert result == "done"


@pytest.mark.asyncio
async def test_pre_llm_hook_adapter_block_short_circuits():
    chain = Chain(phase=Phase.AGENT_REQUEST)
    chain.set_handler(lambda data: _async_return("should-not-reach"))

    class _Blocker(PreLLMHook):
        async def on_pre_llm_call(self, messages, model, tools):
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="blocked")

    adapter = adapt_pre_llm_hook("blocker", _Blocker())
    chain.add_stage("blocker", adapter)

    decision, _ = await chain.run({"messages": [], "model": "m", "tools": None})
    assert decision.action == GuardrailAction.BLOCK
    assert decision.stage_id == "blocker"
    assert decision.reason == "blocked"


@pytest.mark.asyncio
async def test_post_tool_hook_adapter_sanitizes_result():
    """Post-tool SANITIZE flows the modified result downstream."""

    class _Sanitizer(PreToolHook):
        matcher = None

        async def on_pre_tool_call(self, name, arguments, context):
            return GuardrailResult(action=GuardrailAction.ALLOW)

    class _PostHook:
        matcher = None

        async def on_post_tool_call(self, name, result, context):
            return GuardrailResult(
                action=GuardrailAction.SANITIZE,
                modified_data={"result": "REDACTED"},
            )

    chain = Chain(phase=Phase.TOOL_RESULT)
    chain.set_handler(lambda data: _async_return(data))

    adapter = adapt_post_tool_hook("post", _PostHook())
    chain.add_stage("post", adapter)

    decision, result = await chain.run({"name": "anything", "result": "original", "context": None})
    # The post-tool hook returns SANITIZE with modified_data; the chain kernel
    # forwards modified_data downstream via the terminal handler.
    assert decision.action == GuardrailAction.SANITIZE
    assert decision.stage_id == "post"
    # The kernel re-invokes downstream stages with modified_data — terminal
    # sees the sanitized payload.
    assert result == {"result": "REDACTED"}
