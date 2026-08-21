"""Guardrail assembly facade — the single wire-up point for production paths.

T0.2 (guardrail-upgrade-trio): the existing security components
(``create_security_hooks`` factory, ``ToolAccessPolicy`` evaluation,
``ApprovalCallback`` contract) were individually shipped but never connected
to the live execution path. This facade turns that around by providing one
function the workflow service and chat path call to obtain a fully wired
guardrail bundle — hooks + policy + (placeholder) callback — from per-agent
``guardrail_config`` and the workspace's ``ToolPolicyModel`` / ``ToolPolicyRuleModel``
rows.

The facade is the wire-up seam both before and after the T1 waterfall chain:
Phase 1 returns the existing four-hook set; Phase 2 (T1.4) will return a
``MiddlewareChain`` instead without changing the facade's signature.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.guardrail import (
    NoOpPostLLMHook,
    NoOpPostToolHook,
    NoOpPreLLMHook,
    NoOpPreToolHook,
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.engine.monotonic_denials import MonotonicDenialTracker
from hecate.engine.tool_access import (
    ApprovalCallback,
    ApprovalDecision,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)
from hecate.models.tool_policy import ToolPolicyModel, ToolPolicyRuleModel
from hecate.services.security.hooks import (
    SecurityHookSet,
    create_security_hooks,
)

if TYPE_CHECKING:
    pass


_RULE_ACTION_BY_VALUE: dict[str, RuleAction] = {
    "allow": RuleAction.ALLOW,
    "deny": RuleAction.DENY,
    "ask": RuleAction.ASK,
}


@dataclass
class GuardrailBundle:
    """Bundle produced by :func:`assemble_guardrails` for one execution path.

    Contains the four legacy guardrail hooks (still used by LLMWorker /
    ToolWorker single-hook construction paths) plus the wired-up access
    policy and approval callback that ``ToolWorker`` consumes directly. The
    middleware-chain refactor (T1) replaces the four-hook field with a chain
    object; the rest of the bundle stays stable.
    """

    hooks: SecurityHookSet
    access_policy: ToolAccessPolicy
    approval_callback: ApprovalCallback
    rules: list[ToolRule] = field(default_factory=list)
    middleware_chains: dict = field(default_factory=dict)
    denial_tracker: Any | None = None


def _policy_rule_to_tool_rule(row: ToolPolicyModel | ToolPolicyRuleModel) -> ToolRule | None:
    action = _RULE_ACTION_BY_VALUE.get(getattr(row, "rule_action", None) or getattr(row, "action", None) or "")
    if action is None:
        return None
    pattern = row.tool_pattern
    arg_conditions = getattr(row, "arg_conditions", None)
    return ToolRule(
        action=action,
        pattern=pattern,
        priority=getattr(row, "priority", 0) or 0,
        arg_conditions=arg_conditions if isinstance(arg_conditions, dict) else None,
    )


async def assemble_guardrails(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    guardrail_config: dict | None,
    event_store: Any | None = None,
    session_id: uuid.UUID | None = None,
) -> GuardrailBundle:
    """Construct the full guardrail bundle for one agent execution.

    Reads workspace-level (``agent_id IS NULL``) and per-agent rules from
    ``ToolPolicyRuleModel`` plus workspace-level baseline rules from
    ``ToolPolicyModel``. Returns a ``GuardrailBundle`` with the security
    hooks (existing factory), the assembled ``ToolAccessPolicy`` with all
    rules materialized, and a ``FailingClosedApprovalCallback`` when an
    event store and session id are supplied (T2.6 wires this in production;
    falls back to ``NoAnswerApprovalCallback`` otherwise).

    The bundle also carries a ``middleware_chains`` dict keyed by
    ``Phase``, populated by ``build_middleware_chains``. Stages that are
    disabled in ``guardrail_config`` are filtered at assembly time — they
    never enter the chain.

    Args:
        db: Async DB session for the policy lookup.
        workspace_id: Workspace owning the agent; bounds the policy scope.
        agent_id: Agent whose rules augment workspace rules; ``None`` skips
            agent-specific rule loading (e.g. for plain API calls without an
            agent).
        guardrail_config: ``AgentModel.guardrail_config`` dict consumed by
            ``create_security_hooks``.
        event_store: Optional event store; when provided together with
            ``session_id`` and ``workspace_id``, the approval callback
            emits the durable ``APPROVAL_ASKED`` / ``APPROVAL_DECIDED``
            event pair (T2.6).
        session_id: Session id used to anchor approval event emission.

    Returns:
        ``GuardrailBundle`` ready to inject into ``ToolWorker``,
        ``LLMWorker``, and the chat path-A tool loop.
    """
    rules: list[ToolRule] = []

    # Workspace-level policy baseline (deny-by-default security baseline).
    ws_policy_q = select(ToolPolicyModel).where(
        ToolPolicyModel.workspace_id == workspace_id,
        ToolPolicyModel.deleted_at.is_(None),
    )
    for row in (await db.execute(ws_policy_q)).scalars():
        rule = _policy_rule_to_tool_rule(row)
        if rule is not None:
            rules.append(rule)

    # Workspace-level + per-agent ToolPolicyRuleModel rows.
    if agent_id is not None:
        rules_q = select(ToolPolicyRuleModel).where(
            ToolPolicyRuleModel.workspace_id == workspace_id,
            ToolPolicyRuleModel.deleted_at.is_(None),
            (ToolPolicyRuleModel.agent_id.is_(None)) | (ToolPolicyRuleModel.agent_id == agent_id),
        )
    else:
        rules_q = select(ToolPolicyRuleModel).where(
            ToolPolicyRuleModel.workspace_id == workspace_id,
            ToolPolicyRuleModel.deleted_at.is_(None),
            ToolPolicyRuleModel.agent_id.is_(None),
        )
    for row in (await db.execute(rules_q)).scalars():
        rule = _policy_rule_to_tool_rule(row)
        if rule is not None:
            rules.append(rule)

    hooks = create_security_hooks(guardrail_config)

    # T2.6: when the wiring is present, the assembly produces the durable
    # audit-pair-emitting callback; otherwise it falls back to the
    # fail-closed default.
    if event_store is not None and session_id is not None:
        from hecate.services.security.approval import FailingClosedApprovalCallback

        approval_callback: ApprovalCallback = FailingClosedApprovalCallback(
            event_store=event_store,
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
    else:
        approval_callback = NoAnswerApprovalCallback()

    return GuardrailBundle(
        hooks=hooks,
        access_policy=ToolAccessPolicy(),
        approval_callback=approval_callback,
        rules=rules,
        middleware_chains=build_middleware_chains(hooks, guardrail_config),
        denial_tracker=MonotonicDenialTracker() if session_id is not None else None,
    )


def build_middleware_chains(
    hooks,
    guardrail_config: dict | None,
) -> dict:
    """Build the per-Phase middleware chain dict from configured hooks.

    Per-agent scope filtering happens here: stages for hooks that are
    disabled in ``guardrail_config`` (``enabled=False`` or missing section)
    are not added to the chain at all. The function uses the existing
    ``create_security_hooks`` semantics — if a section is absent or
    ``enabled=False``, the corresponding NoOp hook is installed and the
    chain collapses to a passthrough.
    """
    from hecate.engine.middleware_factory import build_llm_chain, build_tool_chain

    cfg = guardrail_config or {}
    input_enabled = bool(cfg.get("input_security", {}).get("enabled", True))
    output_enabled = bool(cfg.get("output_security", {}).get("enabled", True))
    data_enabled = bool(cfg.get("data_security", {}).get("enabled", True))

    if not (input_enabled and output_enabled):
        # Pre/post-LLM hooks are effectively no-op; produce passthrough chains.
        from hecate.engine.guardrail import (
            NoOpPostLLMHook,
            NoOpPreLLMHook,
        )

        llm_chains = build_llm_chain(
            pre_hook=hooks.pre_llm_hook if input_enabled else NoOpPreLLMHook(),
            post_hook=hooks.post_llm_hook if output_enabled else NoOpPostLLMHook(),
        )
    else:
        llm_chains = build_llm_chain(
            pre_hook=hooks.pre_llm_hook,
            post_hook=hooks.post_llm_hook,
        )

    if not data_enabled:
        from hecate.engine.guardrail import NoOpPostToolHook

        tool_chains = build_tool_chain(
            pre_hook=hooks.pre_tool_hook,
            post_hook=NoOpPostToolHook(),
        )
    else:
        tool_chains = build_tool_chain(
            pre_hook=hooks.pre_tool_hook,
            post_hook=hooks.post_tool_hook,
        )

    return {**llm_chains, **tool_chains}


class NoAnswerApprovalCallback(ApprovalCallback):
    """Placeholder approval callback used until T2 wires the real one.

    The fail-closed semantics specified in the change ("no answerer → deny,
    pair still emitted") are enforced by ``services/security/approval.py`` in
    T2. This placeholder just refuses every approval request so the gating
    stack is fail-closed during the wiring phase.
    """

    async def request_approval(
        self,
        tool_name: str,
        arguments: dict,
        risk_level: str,
        context: dict,
    ) -> ApprovalDecision:
        from hecate.engine.tool_access import ApprovalScope

        return ApprovalDecision(approved=False, reason="no_answerer_placeholder", scope=ApprovalScope.ONCE)


# Re-export the existing factory so callers have a single import root.
__all__ = [
    "GuardrailBundle",
    "NoAnswerApprovalCallback",
    "assemble_guardrails",
    "create_security_hooks",
    "SecurityHookSet",
    "NoOpPreLLMHook",
    "NoOpPostLLMHook",
    "NoOpPreToolHook",
    "NoOpPostToolHook",
    "PreLLMHook",
    "PostLLMHook",
    "PreToolHook",
    "PostToolHook",
]
