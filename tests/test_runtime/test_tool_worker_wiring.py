"""T0.2 (guardrail-upgrade-trio) — ToolWorker gating wiring integration tests.

The existing ToolWorker accepts ``access_policy`` and ``approval_callback`` but
its constructor at ``studio/workflows/execution_service.py:587`` did not pass
them. These tests pin the wired-up behavior so production paths (Pregel and
path-A direct tool loop) cannot silently bypass gating.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.ports import RuntimePort
from hecate.runtime.tool_access import (
    AccessDecision,
    ApprovalDecision,
    ApprovalScope,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.runtime.worker import WorkerResult
from hecate.runtime.workers.tool_worker import ToolWorker


class _AllowPort(RuntimePort):
    """Test port that always reports the call as executable."""

    async def llm_invoke(self, *args, **kwargs):  # pragma: no cover - not used
        raise NotImplementedError

    async def tool_execute(self, name, args, context=None):
        return {"executed": True, "name": name, "arguments": args}

    async def knowledge_query(self, query, kb_ids):  # pragma: no cover
        raise NotImplementedError

    async def checkpoint_save(self, state):  # pragma: no cover
        raise NotImplementedError

    async def checkpoint_load(self, checkpoint_id):  # pragma: no cover
        raise NotImplementedError

    async def conversation_load(self, session_id):  # pragma: no cover
        raise NotImplementedError

    async def conversation_save(self, session_id, messages):  # pragma: no cover
        raise NotImplementedError

    async def create_span(self, *args, **kwargs):
        # ToolWorker uses span creation in its hot path; return a no-op ctx.
        return _SpanContext()

    async def end_span(self, *args, **kwargs):
        return None


class _SpanContext:
    span_id = "test-span"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _ApprovalDecisionPort(_AllowPort):
    """Tracks approval invocations."""


async def _messages_with_tool_call(call_id: str, name: str, arguments: dict) -> dict:
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
                        "function": {"name": name, "arguments": _dump_json(arguments)},
                    }
                ],
            },
        ]
    }


def _dump_json(d: dict) -> str:
    import json

    return json.dumps(d)


@pytest.mark.asyncio
async def test_access_policy_deny_short_circuits_execution():
    """When a DENY rule matches, the tool MUST NOT be invoked on the port."""
    port = _AllowPort()
    rules = [ToolRule(action=RuleAction.DENY, pattern="*", priority=100)]
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
    )
    snapshot = await _messages_with_tool_call("tc1", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    assert isinstance(result, WorkerResult)
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert msg["tool_call_id"] == "tc1"
    assert "denied" in msg["content"].lower()


@pytest.mark.asyncio
async def test_require_approval_with_no_callback_fails_closed():
    """REQUIRE_APPROVAL with no callback configured MUST deny (fail-closed)."""
    port = _AllowPort()
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=None,  # explicit no-answerer
    )
    snapshot = await _messages_with_tool_call("tc-ask", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert "no callback" in msg["content"].lower()


class _RecordingApprovalCallback:
    def __init__(self, decision: ApprovalDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[str, dict, str]] = []

    async def request_approval(self, tool_name, arguments, risk_level, context):
        self.calls.append((tool_name, arguments, risk_level))
        return self.decision


@pytest.mark.asyncio
async def test_require_approval_callback_denied_blocks_execution():
    """Callback returning denied stops execution; is_error=True; tool NEVER reaches port."""
    port = _AllowPort()
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]
    callback = _RecordingApprovalCallback(
        ApprovalDecision(approved=False, reason="user said no", scope=ApprovalScope.ONCE)
    )
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=callback,
    )
    snapshot = await _messages_with_tool_call("tc-deny", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert msg["content"] == "Tool call rejected: user said no"
    assert len(callback.calls) == 1


@pytest.mark.asyncio
async def test_require_approval_callback_approved_lets_execution_through():
    """Callback returning approved lets the tool execute normally."""
    port = _AllowPort()
    rules = [ToolRule(action=RuleAction.ASK, pattern="*", priority=100)]
    callback = _RecordingApprovalCallback(ApprovalDecision(approved=True, scope=ApprovalScope.ONCE))
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=rules,
        approval_callback=callback,
    )
    snapshot = await _messages_with_tool_call("tc-ok", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    # Successful execution result is stringified and no is_error flag.
    assert "executed" in msg["content"]
    assert "is_error" not in msg
    assert len(callback.calls) == 1


@pytest.mark.asyncio
async def test_no_policy_no_rules_no_callback_preserves_legacy_behavior():
    """Construction with no gating params yields exactly the pre-change behavior."""
    port = _AllowPort()
    worker = ToolWorker(port=port)
    snapshot = await _messages_with_tool_call("tc-legacy", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    assert "is_error" not in msg
    assert "executed" in msg["content"]


@pytest.mark.asyncio
async def test_context_tool_rules_override_constructor_rules():
    """Caller-supplied context['tool_rules'] takes precedence over constructor rules."""
    port = _AllowPort()
    # Constructor rules would deny everything.
    constructor_rules = [ToolRule(action=RuleAction.DENY, pattern="*", priority=0)]
    # Context rules explicitly allow the call.
    context_rules = [ToolRule(action=RuleAction.ALLOW, pattern="bash", priority=100)]
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        tool_rules=constructor_rules,
    )
    snapshot = await _messages_with_tool_call("tc-ctx", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot={**snapshot, "tool_rules": context_rules},
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    # Context ALLOW wins → tool executes.
    assert "is_error" not in msg
    assert "executed" in msg["content"]


@pytest.mark.asyncio
async def test_check_access_threads_tenant_attribution_to_policy():
    """T2.4 — when an event_store and execution_context are supplied, the
    evaluation context passed to ``ToolAccessPolicy.evaluate`` carries
    ``session_id`` / ``agent_id`` / ``workspace_id`` so the decision
    emitter's ToolDecisionModel rows have tenant attribution.
    """
    from unittest.mock import AsyncMock, MagicMock

    port = _AllowPort()
    fake_store = MagicMock()
    fake_store.append = AsyncMock()
    worker = ToolWorker(
        port=port,
        access_policy=ToolAccessPolicy(),
        event_store=fake_store,
    )

    captured = {}

    class _SpyPolicy:
        def evaluate(self, tool_meta, rules, context, arguments=None):
            captured["context"] = context
            return AccessDecision.EXECUTE

    worker._access_policy = _SpyPolicy()

    snapshot = await _messages_with_tool_call("tc-attr", "bash", {"command": "ls"})
    exec_ctx = {
        "session_id": uuid.uuid4(),
        "superstep": 0,
        "agent_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "on_behalf_of_user": uuid.uuid4(),
    }
    await worker.execute(
        node_id="n1",
        node_config={},
        channel_snapshot=snapshot,
        execution_context=exec_ctx,
    )
    assert "session_id" in captured["context"]
    assert "agent_id" in captured["context"]
    assert "workspace_id" in captured["context"]
    assert "on_behalf_of_user" in captured["context"]
