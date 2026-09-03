"""T0.2 (guardrail-upgrade-trio) — chat path-A guardrail wiring tests.

The direct tool-calling loop at ``api/v1/chat.py::_execute_tool_calls``
previously bypassed all gating. After this change, when an assembled bundle
is supplied, every tool call goes through the same ``ToolAccessPolicy`` /
``ApprovalCallback`` pipeline as the Pregel path. These tests pin that
behavior.
"""

from __future__ import annotations

import pytest

from hecate.channel.api.v1.chat import _execute_tool_calls
from hecate.runtime.tool_access import (
    ApprovalDecision,
    ApprovalScope,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)


class _StubTool:
    def __init__(self, *, raises: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.raises = raises

    async def execute(self, name, arguments, context=None):
        self.calls.append((name, arguments))
        if self.raises:
            raise RuntimeError("boom")
        return {"executed": True, "name": name, "args": arguments}


class _StubRegistry:
    def __init__(self, tool: _StubTool):
        self._tool = tool

    def execute(self, name, arguments, context=None):
        return self._tool.execute(name, arguments, context)


def _tc(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "name": name, "arguments": args}


@pytest.mark.asyncio
async def test_path_a_deny_rule_blocks_execution():
    """DENY rule short-circuits the path-A loop without invoking the registry."""
    tool = _StubTool()
    registry = _StubRegistry(tool)
    rules = [ToolRule(action=RuleAction.DENY, pattern="*", priority=100)]

    results = await _execute_tool_calls(
        registry,
        [_tc("t1", "bash", {"command": "ls"})],
        session_id="s1",
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
    )
    assert results[0]["is_error"] is True
    assert "denied" in results[0]["result"].lower()
    assert tool.calls == []  # never reached the registry


@pytest.mark.asyncio
async def test_path_a_allow_executes_normally():
    """Empty rule set with no DENY lets the call through."""
    tool = _StubTool()
    registry = _StubRegistry(tool)
    results = await _execute_tool_calls(
        registry,
        [_tc("t1", "bash", {"command": "ls"})],
        session_id="s1",
        access_policy=ToolAccessPolicy(),
        tool_rules=[],
    )
    assert results[0]["is_error"] is False
    assert tool.calls == [("bash", {"command": "ls"})]


@pytest.mark.asyncio
async def test_path_a_ask_no_callback_fails_closed():
    """REQUIRE_APPROVAL without a callback MUST deny (fail-closed)."""
    tool = _StubTool()
    registry = _StubRegistry(tool)
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]

    results = await _execute_tool_calls(
        registry,
        [_tc("t-ask", "bash", {"command": "ls"})],
        session_id="s1",
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=None,
    )
    assert results[0]["is_error"] is True
    assert "no callback" in results[0]["result"].lower()
    assert tool.calls == []


class _RecordingCallback:
    def __init__(self, approved: bool, reason: str = "") -> None:
        self.approved = approved
        self.reason = reason
        self.calls: list[tuple[str, dict, str]] = []

    async def request_approval(self, *, tool_name, arguments, risk_level, context):
        self.calls.append((tool_name, arguments, risk_level))
        return ApprovalDecision(
            approved=self.approved,
            reason=self.reason,
            scope=ApprovalScope.ONCE,
        )


@pytest.mark.asyncio
async def test_path_a_callback_approved_lets_call_through():
    """Callback approving the call lets the registry execute it."""
    tool = _StubTool()
    registry = _StubRegistry(tool)
    cb = _RecordingCallback(approved=True)
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]

    results = await _execute_tool_calls(
        registry,
        [_tc("t-ok", "bash", {"command": "ls"})],
        session_id="s1",
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=cb,
    )
    assert results[0]["is_error"] is False
    assert tool.calls == [("bash", {"command": "ls"})]
    assert len(cb.calls) == 1


@pytest.mark.asyncio
async def test_path_a_callback_denied_blocks_execution():
    """Callback denying the call yields an error result and skips the registry."""
    tool = _StubTool()
    registry = _StubRegistry(tool)
    cb = _RecordingCallback(approved=False, reason="user said no")
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]

    results = await _execute_tool_calls(
        registry,
        [_tc("t-deny", "bash", {"command": "ls"})],
        session_id="s1",
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=cb,
    )
    assert results[0]["is_error"] is True
    assert "user said no" in results[0]["result"]
    assert tool.calls == []


@pytest.mark.asyncio
async def test_path_a_no_policy_preserves_legacy_behavior():
    """No access_policy supplied → call reaches the registry as before."""
    tool = _StubTool()
    registry = _StubRegistry(tool)

    results = await _execute_tool_calls(
        registry,
        [_tc("t-legacy", "bash", {"command": "ls"})],
        session_id="s1",
    )
    assert results[0]["is_error"] is False
    assert tool.calls == [("bash", {"command": "ls"})]


@pytest.mark.asyncio
async def test_path_a_dangerous_pattern_deny_takes_precedence_over_user_allow():
    """Built-in DANGEROUS_PATTERNS deny matches win over user ALLOW rules."""
    tool = _StubTool()
    registry = _StubRegistry(tool)
    rules = [
        ToolRule(action=RuleAction.ALLOW, pattern="bash", priority=0),
    ]
    results = await _execute_tool_calls(
        registry,
        [_tc("t-rm", "bash", {"command": "rm -rf /"})],
        session_id="s1",
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
    )
    # rm -rf / hits the built-in DANGEROUS_PATTERNS, DENY wins over user ALLOW.
    assert results[0]["is_error"] is True
    assert tool.calls == []
