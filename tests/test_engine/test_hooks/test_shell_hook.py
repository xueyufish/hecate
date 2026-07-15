"""Tests for ShellCommandHook."""

from __future__ import annotations

from hecate.engine.guardrail import GuardrailAction
from hecate.engine.session_hooks import HookAction
from hecate.engine.shell_hook import ShellCommandHook


async def test_shell_hook_exit_0_allows() -> None:
    """Exit code 0 returns ALLOW."""
    hook = ShellCommandHook(command="echo hello", timeout=5, event_type="PreToolUse")
    result = await hook.execute_guardrail_hook({"tool_name": "test"})
    assert result.action == GuardrailAction.ALLOW


async def test_shell_hook_exit_2_blocks() -> None:
    """Exit code 2 returns BLOCK with stderr as reason."""
    hook = ShellCommandHook(command="echo 'blocked reason' >&2; exit 2", timeout=5, event_type="PreToolUse")
    result = await hook.execute_guardrail_hook({"tool_name": "test"})
    assert result.action == GuardrailAction.BLOCK
    assert "blocked reason" in result.reason


async def test_shell_hook_session_inject_stdout() -> None:
    """SessionStart hook with stdout returns INJECT with context_text."""
    hook = ShellCommandHook(command="echo 'session context'", timeout=5, event_type="SessionStart")
    result = await hook.execute_session_hook({"session_id": "s1"})
    assert result.action == HookAction.INJECT
    assert "session context" in result.context_text


async def test_shell_hook_user_prompt_submit_inject() -> None:
    """UserPromptSubmit hook with stdout returns INJECT."""
    hook = ShellCommandHook(command="echo 'sprint context'", timeout=5, event_type="UserPromptSubmit")
    result = await hook.execute_session_hook({"session_id": "s1", "prompt": "test"})
    assert result.action == HookAction.INJECT
    assert "sprint context" in result.context_text


async def test_shell_hook_timeout_kills_process() -> None:
    """Process exceeding timeout is killed."""
    hook = ShellCommandHook(command="sleep 10", timeout=1, event_type="PreToolUse")
    result = await hook.execute_guardrail_hook({"tool_name": "test"})
    assert result.action == GuardrailAction.ALLOW


async def test_shell_hook_command_failure_returns_allow() -> None:
    """Non-blocking command failure returns ALLOW (not exit 2)."""
    hook = ShellCommandHook(command="exit 1", timeout=5, event_type="PreToolUse")
    result = await hook.execute_guardrail_hook({"tool_name": "test"})
    assert result.action == GuardrailAction.ALLOW


async def test_shell_hook_json_stdin() -> None:
    """Event data is passed as JSON on stdin."""
    hook = ShellCommandHook(
        command="cat",
        timeout=5,
        event_type="SessionStart",
    )
    result = await hook.execute_session_hook({"session_id": "s123", "agent_id": "a1"})
    assert "s123" in result.context_text
    assert "a1" in result.context_text
