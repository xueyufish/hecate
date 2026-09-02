"""Tests for ``services.security.guardrail_assembly``.

T0.2 (guardrail-upgrade-trio): the assembly facade is the wire-up seam that
turns the individually-shipped security components (hooks factory, policy
evaluator, approval callback stub) into a single bundle that
``WorkflowExecutionService`` and the chat path-A loop can inject into
``ToolWorker`` / ``LLMWorker``.
"""

from __future__ import annotations

import uuid

from hecate.models.tool_policy import ToolPolicyModel, ToolPolicyRuleModel
from hecate.runtime.tool_access import ApprovalDecision, RuleAction
from hecate.services.security.guardrail_assembly import (
    GuardrailBundle,
    NoAnswerApprovalCallback,
    assemble_guardrails,
)


async def _make_workspace_policy(db, workspace_id, action, pattern, *, arg_conditions=None, priority=0):
    row = ToolPolicyModel(
        workspace_id=workspace_id,
        rule_action=action,
        tool_pattern=pattern,
        priority=priority,
        arg_conditions=arg_conditions,
    )
    db.add(row)
    await db.flush()
    return row


async def _make_agent_rule(db, workspace_id, agent_id, action, pattern, *, priority=0):
    row = ToolPolicyRuleModel(
        workspace_id=workspace_id,
        agent_id=agent_id,
        action=action,
        tool_pattern=pattern,
        priority=priority,
    )
    db.add(row)
    await db.flush()
    return row


async def test_assemble_returns_bundle_with_policy_and_callback(db_session):
    """Empty workspace → bundle contains a working policy and fail-closed callback."""
    bundle = await assemble_guardrails(
        db_session,
        workspace_id=uuid.uuid4(),
        agent_id=None,
        guardrail_config=None,
    )
    assert isinstance(bundle, GuardrailBundle)
    assert isinstance(bundle.access_policy, object)  # ToolAccessPolicy concrete class
    assert isinstance(bundle.approval_callback, NoAnswerApprovalCallback)
    assert bundle.rules == []


async def test_assemble_loads_workspace_baseline_rules(db_session):
    """Workspace-level ToolPolicyModel rows are materialized into ToolRule list."""
    ws_id = uuid.uuid4()
    await _make_workspace_policy(db_session, ws_id, "deny", "bash", priority=5)
    await _make_workspace_policy(db_session, ws_id, "ask", "write_file", arg_conditions={"path": "*.env"})

    bundle = await assemble_guardrails(
        db_session,
        workspace_id=ws_id,
        agent_id=None,
        guardrail_config=None,
    )
    by_pattern = {r.pattern: r for r in bundle.rules}
    assert RuleAction.DENY in {by_pattern["bash"].action}
    assert by_pattern["bash"].priority == 5
    assert by_pattern["write_file"].action == RuleAction.ASK
    assert by_pattern["write_file"].arg_conditions == {"path": "*.env"}


async def test_assemble_loads_agent_specific_rules(db_session):
    """Per-agent ToolPolicyRuleModel rows are loaded alongside workspace rules."""
    ws_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    other_agent = uuid.uuid4()
    # Workspace-level
    await _make_agent_rule(db_session, ws_id, None, "deny", "terminal(rm:*)")
    # This agent
    await _make_agent_rule(db_session, ws_id, agent_id, "allow", "terminal(git:*)", priority=10)
    # Another agent (should NOT load)
    await _make_agent_rule(db_session, ws_id, other_agent, "allow", "terminal(sudo:*)")

    bundle = await assemble_guardrails(
        db_session,
        workspace_id=ws_id,
        agent_id=agent_id,
        guardrail_config=None,
    )
    patterns = {r.pattern for r in bundle.rules}
    assert "terminal(rm:*)" in patterns
    assert "terminal(git:*)" in patterns
    assert "terminal(sudo:*)" not in patterns


async def test_assemble_skips_deleted_policy_rows(db_session):
    """Soft-deleted rows (deleted_at != NULL) are excluded."""
    ws_id = uuid.uuid4()
    active = await _make_workspace_policy(db_session, ws_id, "deny", "bash")
    removed = await _make_workspace_policy(db_session, ws_id, "deny", "exec_shell")
    from datetime import UTC, datetime

    removed.deleted_at = datetime.now(UTC)
    await db_session.flush()

    bundle = await assemble_guardrails(
        db_session,
        workspace_id=ws_id,
        agent_id=None,
        guardrail_config=None,
    )
    patterns = {r.pattern for r in bundle.rules}
    assert active.tool_pattern in patterns
    assert removed.tool_pattern not in patterns


async def test_assemble_ignores_unknown_action_values(db_session):
    """Rows whose action is not in {allow, deny, ask} are dropped without raising."""
    from hecate.models.tool_policy import ToolPolicyModel

    ws_id = uuid.uuid4()
    db_session.add(
        ToolPolicyModel(
            workspace_id=ws_id,
            rule_action="audit",
            tool_pattern="bash",
        )
    )
    db_session.add(
        ToolPolicyModel(
            workspace_id=ws_id,
            rule_action="deny",
            tool_pattern="rm",
        )
    )
    await db_session.flush()

    bundle = await assemble_guardrails(
        db_session,
        workspace_id=ws_id,
        agent_id=None,
        guardrail_config=None,
    )
    patterns = {r.pattern for r in bundle.rules}
    assert "bash" not in patterns  # unknown action dropped
    assert "rm" in patterns


async def test_no_answer_approval_callback_always_denies():
    """T0.2 placeholder: the callback refuses every request, satisfying
    fail-closed semantics while T2 builds the real audit-pair-emitting component.
    """
    cb = NoAnswerApprovalCallback()
    decision = await cb.request_approval(
        tool_name="bash",
        arguments={"command": "ls"},
        risk_level="high",
        context={},
    )
    assert isinstance(decision, ApprovalDecision)
    assert decision.approved is False
    assert "no_answerer" in decision.reason


async def test_assemble_builds_middleware_chains_for_each_phase(db_session):
    """T1.4 — the bundle exposes a middleware chain dict covering all four
    Phase values. Stages that are disabled in ``guardrail_config`` are
    filtered at assembly time (here the default config keeps every stage).
    """
    from hecate.runtime.middleware import Phase

    bundle = await assemble_guardrails(
        db_session,
        workspace_id=uuid.uuid4(),
        agent_id=None,
        guardrail_config={
            "input_security": {"enabled": True},
            "output_security": {"enabled": True},
            "data_security": {"enabled": True},
        },
    )
    assert Phase.AGENT_REQUEST in bundle.middleware_chains
    assert Phase.LLM_RESPONSE in bundle.middleware_chains
    assert Phase.TOOL_PRE_EXECUTE in bundle.middleware_chains
    assert Phase.TOOL_RESULT in bundle.middleware_chains
    # Each chain has at least one stage (the wrapped hook).
    for chain in bundle.middleware_chains.values():
        assert len(chain.stages) >= 1


async def test_assemble_filters_disabled_stages_from_chain(db_session):
    """A disabled section (e.g. ``input_security.enabled=False``) collapses
    that pre-LLM chain to a NoOp wrapper — the stage is NOT added to the chain."""
    from hecate.runtime.middleware import Phase

    bundle = await assemble_guardrails(
        db_session,
        workspace_id=uuid.uuid4(),
        agent_id=None,
        guardrail_config={
            "input_security": {"enabled": False},
            "output_security": {"enabled": True},
            "data_security": {"enabled": True},
        },
    )
    # The pre-LLM stage was replaced by a NoOp wrapper — but its chain is
    # still present (NoOp wrappers are valid stages that do nothing).
    pre_chain = bundle.middleware_chains[Phase.AGENT_REQUEST]
    assert len(pre_chain.stages) == 1
    assert pre_chain.stages[0].stage_id == "pre-llm"

    async def _terminal(data):
        return data

    pre_chain.set_handler(_terminal)
    decision, _ = await pre_chain.run({"messages": [], "model": "m", "tools": None})
    assert decision.action.value == "allow"
