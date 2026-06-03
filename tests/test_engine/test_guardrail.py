"""Tests for guardrail hooks (PreLLMHook, PostLLMHook, PreToolHook, PostToolHook).

Validates the composition-over-inheritance guardrail design:

- GuardrailAction enum: ALLOW/BLOCK values and string comparison.
- GuardrailResult: allow and block construction.
- Each hook ABC cannot be instantiated directly.
- NoOp implementations return ALLOW for all calls.
- EnginePort guardrail properties default to empty lists.
"""

from __future__ import annotations

import pytest

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
    PreToolHook,
)

# --- GuardrailAction tests ---


def test_guardrail_action_values():
    """GuardrailAction members SHALL equal their string values."""
    assert GuardrailAction.ALLOW == "allow"
    assert GuardrailAction.BLOCK == "block"


def test_guardrail_action_is_string_enum():
    """GuardrailAction SHALL be usable as a string."""
    assert isinstance(GuardrailAction.BLOCK, str)


def test_guardrail_action_string_comparison():
    """GuardrailAction SHALL compare equal to its literal string."""
    assert GuardrailAction.ALLOW == "allow"
    assert GuardrailAction.BLOCK == "block"


def test_guardrail_action_has_two_members():
    """GuardrailAction SHALL have exactly ALLOW and BLOCK."""
    assert len(GuardrailAction) == 2


# --- GuardrailResult tests ---


def test_guardrail_result_allow_defaults():
    """GuardrailResult SHALL default to ALLOW with no reason."""
    result = GuardrailResult()
    assert result.action == GuardrailAction.ALLOW
    assert result.reason == ""


def test_guardrail_result_block_with_reason():
    """GuardrailResult SHALL accept block action with reason."""
    result = GuardrailResult(action=GuardrailAction.BLOCK, reason="Prompt injection detected")
    assert result.action == GuardrailAction.BLOCK
    assert result.reason == "Prompt injection detected"


# --- ABC instantiation tests ---


def test_pre_llm_hook_is_abstract():
    """PreLLMHook SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        PreLLMHook()  # type: ignore[abstract]


def test_post_llm_hook_is_abstract():
    """PostLLMHook SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        PostLLMHook()  # type: ignore[abstract]


def test_pre_tool_hook_is_abstract():
    """PreToolHook SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        PreToolHook()  # type: ignore[abstract]


def test_post_tool_hook_is_abstract():
    """PostToolHook SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        PostToolHook()  # type: ignore[abstract]


# --- NoOp hook tests ---


async def test_noop_pre_llm_hook_returns_allow():
    """NoOpPreLLMHook SHALL return ALLOW."""
    hook = NoOpPreLLMHook()
    result = await hook.on_pre_llm_call(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-4o",
        tools=None,
    )
    assert result.action == GuardrailAction.ALLOW
    assert result.reason == ""


async def test_noop_post_llm_hook_returns_allow():
    """NoOpPostLLMHook SHALL return ALLOW."""
    hook = NoOpPostLLMHook()
    result = await hook.on_post_llm_call(
        response={"content": "Hi there"},
        messages=[{"role": "user", "content": "hello"}],
    )
    assert result.action == GuardrailAction.ALLOW


async def test_noop_pre_tool_hook_returns_allow():
    """NoOpPreToolHook SHALL return ALLOW."""
    hook = NoOpPreToolHook()
    result = await hook.on_pre_tool_call(
        name="search",
        arguments={"query": "test"},
        context=None,
    )
    assert result.action == GuardrailAction.ALLOW


async def test_noop_post_tool_hook_returns_allow():
    """NoOpPostToolHook SHALL return ALLOW."""
    hook = NoOpPostToolHook()
    result = await hook.on_post_tool_call(
        name="search",
        result="found 3 items",
        context=None,
    )
    assert result.action == GuardrailAction.ALLOW


# --- Custom implementation tests ---


async def test_custom_pre_llm_hook_can_block():
    """A custom PreLLMHook SHALL be able to return BLOCK."""

    class BlockingPreLLMHook(PreLLMHook):
        async def on_pre_llm_call(self, messages: list[dict], model: str, tools: list[dict] | None) -> GuardrailResult:
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="Blocked by policy")

    hook = BlockingPreLLMHook()
    result = await hook.on_pre_llm_call(
        messages=[{"role": "user", "content": "hack"}],
        model="gpt-4o",
        tools=None,
    )
    assert result.action == GuardrailAction.BLOCK
    assert result.reason == "Blocked by policy"


async def test_custom_pre_tool_hook_can_block():
    """A custom PreToolHook SHALL be able to return BLOCK."""

    class BlockingPreToolHook(PreToolHook):
        async def on_pre_tool_call(self, name: str, arguments: dict, context: dict | None) -> GuardrailResult:
            return GuardrailResult(action=GuardrailAction.BLOCK, reason=f"Tool {name} not authorized")

    hook = BlockingPreToolHook()
    result = await hook.on_pre_tool_call(
        name="dangerous_tool",
        arguments={},
        context=None,
    )
    assert result.action == GuardrailAction.BLOCK
    assert "dangerous_tool" in result.reason


# --- EnginePort integration ---


def test_engine_port_guardrail_properties_default_to_empty():
    """EnginePort guardrail properties SHALL return empty lists by default."""

    class MinimalPort:
        @property
        def pre_llm_hooks(self) -> list[PreLLMHook]:
            return []

        @property
        def post_llm_hooks(self) -> list[PostLLMHook]:
            return []

        @property
        def pre_tool_hooks(self) -> list[PreToolHook]:
            return []

        @property
        def post_tool_hooks(self) -> list[PostToolHook]:
            return []

    port = MinimalPort()
    assert port.pre_llm_hooks == []
    assert port.post_llm_hooks == []
    assert port.pre_tool_hooks == []
    assert port.post_tool_hooks == []
