"""T0.2 (guardrail-upgrade-trio) — end-to-end path consistency tests.

The guardrail spec requires that the Pregel path and the path-A direct tool
loop produce the same gating decision for an identical (policy, rules,
tool-call) triple. These tests pin that contract — a CRITICAL-risk tool
with no approval callback MUST be denied on both paths; legacy behavior
(``access_policy is None``) MUST be preserved on both paths.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.api.v1.chat import _execute_tool_calls
from hecate.runtime.tool_access import (
    ApprovalDecision,
    ApprovalScope,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.runtime.workers.tool_worker import ToolWorker


def _tc_payload(call_id: str, name: str, args: dict) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            },
        ]
    }


def _deny_all_critical_policy() -> tuple[ToolAccessPolicy, list[ToolRule]]:
    """Policy: ASK on CRITICAL risk, no callback configured → fail-closed deny."""
    policy = ToolAccessPolicy()
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]
    return policy, rules


class _StubPort:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        return {"ok": True, "name": name, "args": args}

    async def create_span(self, *args, **kwargs):
        class _CM:
            span_id = "test-span"

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_both_paths_deny_critical_risk_when_no_callback_configured():
    """Identical gating params produce identical deny behavior on both paths."""
    policy, rules = _deny_all_critical_policy()
    # Override the per-tool risk override so the chat path also evaluates ASK.
    risk_overrides = {"bash": "critical"}

    # Path A: chat direct tool loop.
    class _Registry:
        async def execute(self, name, arguments, context=None):
            return {"ok": True}

    results_a = await _execute_tool_calls(
        _Registry(),
        [{"id": "t-A", "name": "bash", "arguments": {"command": "ls"}}],
        session_id="s-A",
        access_policy=policy,
        tool_rules=rules,
        approval_callback=None,
        risk_overrides=risk_overrides,
    )
    assert results_a[0]["is_error"] is True
    assert "no callback" in results_a[0]["result"].lower()

    # Pregel path: ToolWorker with the same policy/rules/callback=None.
    fake_store = MagicMock()
    fake_store.append = AsyncMock()
    port = _StubPort()
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=None,
        event_store=fake_store,
    )
    snapshot = _tc_payload("t-B", "bash", {"command": "ls"})
    result_b = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0, "risk_level": "critical"},
    )
    msg_b = result_b.channel_updates["messages"][0]
    assert msg_b["is_error"] is True
    assert "no callback" in msg_b["content"].lower()
    assert port.calls == []  # path B also never reached the port


@pytest.mark.asyncio
async def test_both_paths_legacy_behavior_unchanged_when_policy_absent():
    """Without access_policy, both paths behave like pre-change (no gating)."""

    class _Registry:
        def __init__(self):
            self.calls = 0

        async def execute(self, name, arguments, context=None):
            self.calls += 1
            return {"ok": True, "name": name}

    registry = _Registry()
    results_a = await _execute_tool_calls(
        registry,
        [{"id": "t-A", "name": "bash", "arguments": {"command": "ls"}}],
        session_id="s-A",
    )
    assert results_a[0]["is_error"] is False
    assert registry.calls == 1

    fake_store = MagicMock()
    fake_store.append = AsyncMock()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=fake_store)
    snapshot = _tc_payload("t-B", "bash", {"command": "ls"})
    result_b = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg_b = result_b.channel_updates["messages"][0]
    assert "is_error" not in msg_b
    assert len(port.calls) == 1


@pytest.mark.asyncio
async def test_both_paths_approve_when_callback_returns_approved():
    """Identical approved callback yields identical successful execution."""
    policy = ToolAccessPolicy()
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]

    class _Approve:
        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            return ApprovalDecision(approved=True, scope=ApprovalScope.ONCE)

    class _Registry:
        async def execute(self, name, arguments, context=None):
            return {"ok": True, "name": name}

    results_a = await _execute_tool_calls(
        _Registry(),
        [{"id": "t-A", "name": "bash", "arguments": {"command": "ls"}}],
        session_id="s-A",
        access_policy=policy,
        tool_rules=rules,
        approval_callback=_Approve(),
    )
    assert results_a[0]["is_error"] is False

    fake_store = MagicMock()
    fake_store.append = AsyncMock()
    port = _StubPort()
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=_Approve(),
        event_store=fake_store,
    )
    snapshot = _tc_payload("t-B", "bash", {"command": "ls"})
    result_b = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg_b = result_b.channel_updates["messages"][0]
    assert "is_error" not in msg_b
    assert len(port.calls) == 1
