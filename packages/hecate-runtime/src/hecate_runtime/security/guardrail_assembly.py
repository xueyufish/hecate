"""Guardrail assembly facade — the kernel-pure wire-up point.

The security components (``create_security_hooks`` factory, ``ToolAccessPolicy``
evaluation, ``ApprovalCallback`` contract) are assembled here into the
per-Phase middleware chains that the workflow service and chat path consume.

Since step5b the kernel consumes **already-loaded** ``ToolRule`` rows; the
DB-backed policy loading, org derivation, and finding-writer construction
live in the platform bridge
(``hecate.core.composition.guardrail_platform``), which adapts this facade
for the chat path. A standalone host loads rules from its own local policy
source and calls this facade directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from hecate_runtime.monotonic_denials import MonotonicDenialTracker
from hecate_runtime.security.hooks import create_security_hooks
from hecate_runtime.tool_access import (
    ApprovalCallback,
    ApprovalDecision,
    RuleAction,
    ToolAccessPolicy,
    ToolRule,
)

_RULE_ACTION_BY_VALUE: dict[str, RuleAction] = {
    "allow": RuleAction.ALLOW,
    "deny": RuleAction.DENY,
    "ask": RuleAction.ASK,
}


@dataclass
class GuardrailBundle:
    """Bundle produced by :func:`assemble_guardrails` for one execution path.

    Carries the per-Phase middleware chains (built from the configured
    security hooks), the wired-up access policy, the approval callback,
    and the per-session denial tracker that ``ToolWorker`` / ``LLMWorker``
    / the chat path-A tool loop consume.
    """

    access_policy: ToolAccessPolicy
    approval_callback: ApprovalCallback
    rules: list[ToolRule] = field(default_factory=list)
    middleware_chains: dict = field(default_factory=dict)
    denial_tracker: Any | None = None


def tool_rule_from_policy_row(row: Any) -> ToolRule | None:
    action = _RULE_ACTION_BY_VALUE.get(getattr(row, "rule_action", None) or getattr(row, "action", None) or "")
    if action is None:
        return None
    pattern = getattr(row, "tool_pattern", None)
    arg_conditions = getattr(row, "arg_conditions", None)
    return ToolRule(
        action=action,
        pattern=pattern,
        priority=getattr(row, "priority", 0) or 0,
        arg_conditions=arg_conditions if isinstance(arg_conditions, dict) else None,
    )


async def assemble_guardrails(
    rules: list[ToolRule],
    guardrail_config: dict | None,
    event_store: Any | None = None,
    session_id: uuid.UUID | None = None,
    dlp_scanner: Any = None,
    finding_writer: Any | None = None,
    workspace_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> GuardrailBundle:
    """Construct the guardrail bundle from already-loaded policy rules.

    Args:
        rules: Materialized ``ToolRule`` list — loaded by the caller from its
            policy source (platform DB adapter or a standalone host's local
            policy). Use ``tool_rule_from_policy_row`` to convert raw policy
            rows.
        guardrail_config: ``AgentModel.guardrail_config`` dict consumed by
            ``create_security_hooks``.
        event_store: Optional event store; when provided together with
            ``session_id`` and ``workspace_id``, the approval callback
            emits the durable ``APPROVAL_ASKED`` / ``APPROVAL_DECIDED``
            event pair (T2.6).
        session_id: Session id used to anchor approval event emission.
        dlp_scanner: Optional DLP scanner injected into the output hooks.
        finding_writer: Optional platform finding writer (ops adapter);
            the kernel never constructs it itself.
        workspace_id: Workspace owning the agent; anchors the approval
            callback.
        agent_id: Agent whose execution this bundle guards.

    Returns:
        ``GuardrailBundle`` ready to inject into ``ToolWorker``,
        ``LLMWorker``, and the chat path-A tool loop.
    """
    hooks = create_security_hooks(guardrail_config, dlp_scanner=dlp_scanner, finding_writer=finding_writer)

    # T2.6: when the wiring is present, the assembly produces the durable
    # audit-pair-emitting callback; otherwise it falls back to the
    # fail-closed default.
    if event_store is not None and session_id is not None:
        from hecate_runtime.security.approval import FailingClosedApprovalCallback

        approval_callback: ApprovalCallback = FailingClosedApprovalCallback(
            event_store=event_store,
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
    else:
        approval_callback = NoAnswerApprovalCallback()

    return GuardrailBundle(
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
    from hecate_runtime.middleware_factory import build_llm_chain, build_tool_chain

    cfg = guardrail_config or {}
    input_enabled = bool(cfg.get("input_security", {}).get("enabled", True))
    output_enabled = bool(cfg.get("output_security", {}).get("enabled", True))
    data_enabled = bool(cfg.get("data_security", {}).get("enabled", True))

    if not (input_enabled and output_enabled):
        # Pre/post-LLM hooks are effectively no-op; produce passthrough chains.
        from hecate_runtime.guardrail import (
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
        from hecate_runtime.guardrail import NoOpPostToolHook

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
        from hecate_runtime.tool_access import ApprovalScope

        return ApprovalDecision(approved=False, reason="no_answerer_placeholder", scope=ApprovalScope.ONCE)


# Re-exported for callers that use the assembly as their single import root.
__all__ = [
    "GuardrailBundle",
    "NoAnswerApprovalCallback",
    "assemble_guardrails",
]
