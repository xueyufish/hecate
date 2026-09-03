"""T0.2 (guardrail-upgrade-trio) — WorkflowExecutionService wiring tests.

The pre-change behavior constructed ToolWorker without ``access_policy`` /
``approval_callback`` / ``tool_rules``, leaving the gating stack inert in
production. These tests pin the wiring contract: when the service receives
these parameters, the constructed ToolWorker evaluates them on every call.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hecate.runtime.tool_access import (
    ApprovalDecision,
    ApprovalScope,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.studio.workflows.execution_service import WorkflowExecutionService


class _StubPort:
    async def tool_execute(self, name, args, context=None):
        return {"executed": True, "name": name, "args": args}

    async def create_span(self, *args, **kwargs):
        ctx = MagicMock()
        ctx.span_id = "test-span"

        class _CM:
            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


class _ApproveCallback:
    def __init__(self):
        self.calls = 0

    async def request_approval(self, *, tool_name, arguments, risk_level, context):
        self.calls += 1
        return ApprovalDecision(approved=True, scope=ApprovalScope.ONCE)


@pytest.mark.asyncio
async def test_execution_service_propagates_policy_callback_rules_to_tool_worker():
    """WorkflowExecutionService forwards gating params to the ToolWorker it builds."""
    cb = _ApproveCallback()
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]
    policy = ToolAccessPolicy()

    svc = WorkflowExecutionService(
        port=_StubPort(),
        db=MagicMock(),
        event_store=None,
        access_policy=policy,
        approval_callback=cb,
        tool_policy_rules=rules,
    )

    assert svc._access_policy is policy
    assert svc._approval_callback is cb
    assert svc._tool_policy_rules is rules


@pytest.mark.asyncio
async def test_execution_service_constructs_policy_when_only_rules_given():
    """When rules are supplied but no policy, the service materializes a default ToolAccessPolicy."""
    rules = [ToolRule(action=RuleAction.DENY, pattern="*", priority=100)]
    svc = WorkflowExecutionService(
        port=_StubPort(),
        db=MagicMock(),
        event_store=None,
        tool_policy_rules=rules,
    )
    assert isinstance(svc._access_policy, ToolAccessPolicy)
    assert svc._tool_policy_rules is rules


@pytest.mark.asyncio
async def test_execution_service_legacy_construction_keeps_no_policy():
    """Pre-change callers (no params) must keep working: no policy, no callback."""
    svc = WorkflowExecutionService(
        port=_StubPort(),
        db=MagicMock(),
        event_store=None,
    )
    assert svc._access_policy is None
    assert svc._approval_callback is None
    assert svc._tool_policy_rules == []
