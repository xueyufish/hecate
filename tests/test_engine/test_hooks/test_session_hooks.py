"""Tests for session lifecycle event hooks."""

from __future__ import annotations

from hecate.engine.session_hooks import (
    HookAction,
    HookResult,
    NoOpPreCompactHook,
    NoOpSessionEndHook,
    NoOpSessionStartHook,
    NoOpUserPromptSubmitHook,
)


async def test_noop_session_start_allows() -> None:
    """No-op session start hook returns ALLOW."""
    hook = NoOpSessionStartHook()
    result = await hook.on_session_start("s1", "a1", "startup")
    assert result.action == HookAction.ALLOW


async def test_noop_session_end_allows() -> None:
    """No-op session end hook returns ALLOW."""
    hook = NoOpSessionEndHook()
    result = await hook.on_session_end("s1", "a1", "close")
    assert result.action == HookAction.ALLOW


async def test_noop_user_prompt_submit_allows() -> None:
    """No-op user prompt submit hook returns ALLOW."""
    hook = NoOpUserPromptSubmitHook()
    result = await hook.on_user_prompt_submit("s1", "test prompt")
    assert result.action == HookAction.ALLOW


async def test_noop_pre_compact_allows() -> None:
    """No-op pre-compact hook returns ALLOW."""
    hook = NoOpPreCompactHook()
    result = await hook.on_pre_compact("s1", "auto")
    assert result.action == HookAction.ALLOW


def test_hook_result_defaults() -> None:
    """HookResult has sensible defaults."""
    result = HookResult()
    assert result.action == HookAction.ALLOW
    assert result.context_text == ""
    assert result.reason == ""


def test_hook_result_block_with_reason() -> None:
    """HookResult BLOCK includes a reason."""
    result = HookResult(action=HookAction.BLOCK, reason="Invalid prompt")
    assert result.action == HookAction.BLOCK
    assert result.reason == "Invalid prompt"


def test_hook_result_inject_with_context() -> None:
    """HookResult INJECT includes context text."""
    result = HookResult(action=HookAction.INJECT, context_text="git status output")
    assert result.action == HookAction.INJECT
    assert result.context_text == "git status output"
