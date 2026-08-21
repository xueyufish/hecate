"""T3.3 — monotonic-denial regression tests.

Pin the contract that a denied ``tool_call_id`` cannot be resurrected by
re-running the policy pipeline. Once denied, the call stays denied within
the same session.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.engine.monotonic_denials import MonotonicDenialTracker
from hecate.engine.tool_access import RuleAction, ToolAccessPolicy, ToolRule
from hecate.engine.workers.tool_worker import ToolWorker


class _StubPort:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        return {"executed": True}

    async def create_span(self, *args, **kwargs):
        class _CM:
            span_id = "span"

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


def _payload(call_id: str, name: str, args: dict) -> dict:
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


@pytest.mark.asyncio
async def test_denied_call_is_not_resurrected_by_later_allow_rule():
    """After a DENY, switching the ruleset to ALLOW MUST NOT resurrect the call."""
    port = _StubPort()
    tracker = MonotonicDenialTracker()

    deny_rules = [ToolRule(action=RuleAction.DENY, pattern="*", priority=100)]
    allow_rules = [ToolRule(action=RuleAction.ALLOW, pattern="*", priority=100)]

    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=deny_rules,
        denial_tracker=tracker,
        event_store=MagicMock(append=AsyncMock()),
    )
    snapshot = _payload("tc-monotonic", "bash", {"command": "ls"})
    exec_ctx = {"session_id": uuid.uuid4(), "superstep": 0}

    first = await worker.execute(node_id="n", node_config={}, channel_snapshot=snapshot, execution_context=exec_ctx)
    assert first.channel_updates["messages"][0]["is_error"] is True
    assert port.calls == []
    assert tracker.is_denied("tc-monotonic")

    # Swap the ruleset to ALLOW-only. The tracker MUST still refuse.
    worker._tool_rules = allow_rules
    second = await worker.execute(node_id="n", node_config={}, channel_snapshot=snapshot, execution_context=exec_ctx)
    msg = second.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert "denied" in msg["content"].lower()
    assert port.calls == []  # still refused


@pytest.mark.asyncio
async def test_denied_call_is_not_resurrected_by_callback_approval():
    """After a DENY, even an approving callback MUST NOT authorize the same call."""
    from hecate.engine.tool_access import ApprovalDecision, ApprovalScope

    port = _StubPort()
    tracker = MonotonicDenialTracker()
    deny_rules = [ToolRule(action=RuleAction.DENY, pattern="*", priority=100)]
    ask_rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]

    class _Approve:
        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            return ApprovalDecision(approved=True, scope=ApprovalScope.ONCE)

    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=deny_rules,
        approval_callback=_Approve(),
        denial_tracker=tracker,
        event_store=MagicMock(append=AsyncMock()),
    )
    snapshot = _payload("tc-revive", "bash", {"command": "ls"})
    exec_ctx = {"session_id": uuid.uuid4(), "superstep": 0}

    first = await worker.execute(node_id="n", node_config={}, channel_snapshot=snapshot, execution_context=exec_ctx)
    assert first.channel_updates["messages"][0]["is_error"] is True
    assert tracker.is_denied("tc-revive")

    # Now swap to ASK + an always-approve callback — still refused.
    worker._tool_rules = ask_rules
    second = await worker.execute(node_id="n", node_config={}, channel_snapshot=snapshot, execution_context=exec_ctx)
    assert second.channel_updates["messages"][0]["is_error"] is True
    assert port.calls == []


@pytest.mark.asyncio
async def test_different_tool_call_ids_are_independent():
    """A denial of tc-A does not block tc-B in the same session."""
    port = _StubPort()
    tracker = MonotonicDenialTracker()

    deny_a = [ToolRule(action=RuleAction.DENY, pattern="bash", priority=100)]
    allow_b = []  # no rules — falls through to risk-level fallback

    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=deny_a,
        denial_tracker=tracker,
        event_store=MagicMock(append=AsyncMock()),
    )

    # A is denied.
    a = _payload("tc-a", "bash", {"command": "rm -rf /"})
    res_a = await worker.execute(
        node_id="n", node_config={}, channel_snapshot=a, execution_context={"session_id": uuid.uuid4(), "superstep": 0}
    )
    assert res_a.channel_updates["messages"][0]["is_error"] is True

    # B is independent.
    worker._tool_rules = allow_b
    b = _payload("tc-b", "read_file", {"path": "x"})
    res_b = await worker.execute(
        node_id="n", node_config={}, channel_snapshot=b, execution_context={"session_id": uuid.uuid4(), "superstep": 0}
    )
    # B's path: not denied, no rules → EXECUTE/EXECUTE_SANDBOX/REQUIRE_APPROVAL → execution.
    b_msg = res_b.channel_updates["messages"][0]
    assert "is_error" not in b_msg or not b_msg["is_error"]
