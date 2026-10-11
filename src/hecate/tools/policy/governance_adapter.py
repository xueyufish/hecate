"""Adapter from the governance ``PolicyProfile`` seam to the tool pipeline.

The platform keeps ``ToolPolicyPipeline`` as its policy engine; this adapter
exposes it through the governance ``PolicyProfile`` interface so
``GovernanceService`` (shared with the standalone runner) judges platform tool
calls with the same rules, and both entry paths speak one decision language.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hecate_governance.action import ActionDecision
from hecate_governance.policy import PolicyContext as GovernanceContext
from hecate_governance.policy import PolicyOutcome, PolicyProfile

from hecate.tools.policy import PolicyContext, PolicyDecision, ToolPolicyPipeline
from hecate.tools.policy.policy_layers import (
    PluginAvailabilityLayer,
    ProfileLayer,
    SecurityLayer,
    VisibilityLayer,
)

PROFILE_VERSION = "tool-pipeline-1"


def default_platform_pipeline(rules: list[dict[str, Any]] | None = None) -> ToolPolicyPipeline:
    """The standard platform pipeline used for governance evaluation."""
    return ToolPolicyPipeline(
        layers=[
            PluginAvailabilityLayer(),
            ProfileLayer(rules=rules),
            VisibilityLayer(),
            SecurityLayer(),
        ]
    )


class PipelinePolicyProfile(PolicyProfile):
    """Evaluate governance requests through the tool policy pipeline.

    ``tool_meta_lookup`` (optional) supplies registry metadata (risk level,
    approval flags) for the evaluated tool; without it a minimal ToolInfo is
    used so unconditional rules still evaluate.
    """

    def __init__(
        self,
        pipeline: ToolPolicyPipeline | None = None,
        *,
        tool_meta_lookup: Callable[[str], dict[str, Any]] | None = None,
        rules: list[dict[str, Any]] | None = None,
        allow_sandbox: bool = True,
    ) -> None:
        self._allow_sandbox = allow_sandbox
        self._pipeline = pipeline or default_platform_pipeline(rules)
        self._tool_meta_lookup = tool_meta_lookup

    @property
    def version(self) -> str:
        return PROFILE_VERSION

    @property
    def pipeline(self) -> ToolPolicyPipeline:
        """The wrapped pipeline (composition may reconfigure rules on it)."""
        return self._pipeline

    async def evaluate(self, context: GovernanceContext) -> PolicyOutcome:
        meta = (
            dict(context.tool_metadata)
            if context.tool_metadata is not None
            else (
                dict(self._tool_meta_lookup(context.tool_name))
                if self._tool_meta_lookup
                else {"name": context.tool_name}
            )
        )
        meta.setdefault("name", context.tool_name)
        tool_info = meta
        platform_context = self._to_platform_context(context)
        decision, _ = self._pipeline.evaluate_execution(_info_from(tool_info), platform_context)
        return PolicyOutcome(
            decision=ActionDecision.DENY
            if decision is PolicyDecision.EXECUTE_SANDBOX and not self._allow_sandbox
            else _map_decision(decision),
            policy_version=PROFILE_VERSION,
            reason=decision.value,
        )

    @staticmethod
    def _to_platform_context(context: GovernanceContext) -> PolicyContext:
        return PolicyContext(
            agent_id=context.principal.agent_principal if context.principal else None,
            workspace_id=context.workspace_id,
            rules=list(context.runtime_rules),
            arguments=dict(context.arguments),
            actor=context.principal.actor if context.principal else None,
            agent_principal=context.principal.agent_principal if context.principal else None,
            on_behalf_of=context.principal.on_behalf_of if context.principal else None,
            delegation_id=context.principal.delegation_id if context.principal else None,
            data_domains=context.principal.domains if context.principal else (),
            deployment_id=context.deployment_id,
            invocation_id=context.invocation_id,
            task_ref=context.task_ref,
            metered=context.metered,
            budget_exhausted=context.budget is not None and context.budget.value != "ok",
        )


def _info_from(tool: dict[str, Any]) -> Any:
    from hecate.tools.policy import ToolInfo

    return ToolInfo(
        name=tool.get("name", ""),
        source=tool.get("source", "builtin"),
        risk_level=str(tool.get("risk_level", "low")).lower(),
        approval_required=bool(tool.get("approval_required", False)),
        sandbox_enabled=bool(tool.get("sandbox_enabled", False)),
        available_when=tool.get("available_when"),
        plugin_name=tool.get("plugin_name"),
        mcp_server=tool.get("mcp_server"),
        tool_def=tool,
    )


def _map_decision(decision: PolicyDecision) -> ActionDecision:
    if decision in (PolicyDecision.ALLOW, PolicyDecision.PASSTHROUGH, PolicyDecision.EXECUTE_SANDBOX):
        return ActionDecision.ALLOW
    if decision is PolicyDecision.REQUIRE_APPROVAL:
        return ActionDecision.REQUIRE_APPROVAL
    # DENY and HIDE both stop execution; sandbox routing is an execution
    # detail the governance decision layer does not model.
    return ActionDecision.DENY
