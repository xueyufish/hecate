"""F3 — approval audit pair emitted end-to-end.

Approval is a safety-critical seam: every REQUIRE_APPROVAL tool call must
produce the durable APPROVAL_ASKED / APPROVAL_DECIDED event pair, even on
the fail-closed (no-answerer) path. A regression here silently disables
human-in-the-loop for risky operations across the platform — a class of
bug unit tests cannot catch because they exercise the approval callback
in isolation rather than against a real event store.

Two layers are asserted:

1. The policy path resolves ASK rules (or high-risk tools) to
   REQUIRE_APPROVAL — guards the rest of the test from passing for the
   wrong reason.
2. The FailingClosedApprovalCallback emits the audit pair in the
   store on every request, including the no-answerer deny path, keyed
   by the same session_id and tool_call_id.
"""

from __future__ import annotations

import uuid

from hecate.engine.eventstore import EventType, InMemoryEventStore
from hecate.engine.tool_access import (
    AccessDecision,
    ApprovalScope,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.services.security.approval import FailingClosedApprovalCallback


def _events_for(store: InMemoryEventStore, session_id: uuid.UUID) -> list:
    """Read the InMemoryEventStore backing dict directly — avoids re-entering
    asyncio from a test running in the auto-asyncio loop."""
    return list(store._store.get(session_id, []))


def test_ask_rule_evaluates_require_approval() -> None:
    """Sanity: the test-fixture rule actually triggers REQUIRE_APPROVAL.

    Without this, the audit-pair assertion below would silently pass for
    unrelated reasons (e.g. ALLOW path) — keeping this guard makes the
    intent explicit. Note: the policy needs both an ASK rule and
    approval_required=True (or risk_level >= high) to trigger; a low-risk
    tool with no rule returns EXECUTE, which is the path this guard
    detects as a test-fixture mistake.
    """
    policy = ToolAccessPolicy()
    decision = policy.evaluate(
        {"name": "risky_tool", "risk_level": "high", "approval_required": True},
        [ToolRule(action=RuleAction.ASK, pattern=r"^risky_")],
        {"workspace_root": "/tmp", "tool_name": "risky_tool"},  # noqa: S108 — sentinel, not a real temp path
        arguments={"target": "db"},
    )
    assert decision == AccessDecision.REQUIRE_APPROVAL


async def test_failing_closed_callback_emits_audit_pair() -> None:
    """FailingClosedApprovalCallback emits ASKED + DECIDED in the no-answerer
    deny path, keyed by the same session_id and tool_call_id.

    Production wiring uses this exact fail-closed default — without the
    pair, every denied-by-default risky call is invisible to the audit
    log and to 8.20 Run Replay.
    """
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    store = InMemoryEventStore()
    callback = FailingClosedApprovalCallback(
        event_store=store,
        session_id=session_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )

    decision = await callback.request_approval(
        tool_name="risky_tool",
        arguments={"tool_call_id": "tc-audit-1", "target": "production-db"},
        risk_level="high",
        context={"tool_call_id": "tc-audit-1"},
    )

    # No-answerer backend refuses every request — production fail-closed default.
    assert decision.approved is False
    assert decision.scope == ApprovalScope.ONCE

    emitted = _events_for(store, session_id)
    asked = [e for e in emitted if e.event_type == EventType.APPROVAL_ASKED]
    decided = [e for e in emitted if e.event_type == EventType.APPROVAL_DECIDED]

    assert len(asked) == 1, f"expected 1 APPROVAL_ASKED, got {len(asked)}"
    assert len(decided) == 1, f"expected 1 APPROVAL_DECIDED, got {len(decided)}"
    assert asked[0].session_id == session_id
    assert decided[0].session_id == session_id
    assert asked[0].payload["tool_call_id"] == "tc-audit-1"
    assert asked[0].payload["tool_name"] == "risky_tool"
    assert asked[0].payload["risk_level"] == "high"
    assert decided[0].payload["approved"] is False
    assert decided[0].payload["scope"] == ApprovalScope.ONCE.value


async def test_audit_pair_emitted_per_request_even_when_denied() -> None:
    """Two consecutive calls produce two ASKED + two DECIDED events.

    Guards against a regression where the callback caches a denial and
    silently stops emitting — which would break the audit log for the
    very requests that matter most (the ones users repeatedly try).
    """
    session_id = uuid.uuid4()
    store = InMemoryEventStore()
    callback = FailingClosedApprovalCallback(
        event_store=store,
        session_id=session_id,
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
    )

    for tc_id in ("tc-1", "tc-2"):
        await callback.request_approval(
            tool_name="risky_tool",
            arguments={"tool_call_id": tc_id},
            risk_level="high",
            context={"tool_call_id": tc_id},
        )

    emitted = _events_for(store, session_id)
    asked_ids = sorted(e.payload["tool_call_id"] for e in emitted if e.event_type == EventType.APPROVAL_ASKED)
    decided_ids = sorted(e.payload["tool_call_id"] for e in emitted if e.event_type == EventType.APPROVAL_DECIDED)
    assert asked_ids == ["tc-1", "tc-2"]
    assert decided_ids == ["tc-1", "tc-2"]
