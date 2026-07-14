"""Concrete policy layer implementations for the tool policy pipeline.

Five layers in evaluation order:
0. PluginAvailabilityLayer — check plugin/MCP server is enabled
1. ProfileLayer — per-agent/workspace declarative rules (glob + arg conditions)
2. VisibilityLayer — available_when expression evaluation (wraps ToolGateEvaluator)
3. SecurityLayer — wraps existing ToolAccessPolicy (5-layer security evaluation)
4. ModeLayer — PermissionMode override (DEFAULT/RESTRICTED/AUDIT)
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from hecate.engine.policy_pipeline import (
    PermissionMode,
    PolicyContext,
    PolicyDecision,
    PolicyLayer,
    ToolInfo,
)
from hecate.engine.tool_access import AccessDecision, ToolAccessPolicy
from hecate.engine.tool_gate import ToolGateEvaluator

logger = logging.getLogger(__name__)


class PluginAvailabilityLayer(PolicyLayer):
    """Layer 0: Check plugin/MCP server availability.

    Args:
        plugin_status_fn: Callable that returns True if a plugin is enabled.
        mcp_server_fn: Callable that returns True if an MCP server is registered.
    """

    def __init__(
        self,
        plugin_status_fn: Any = None,
        mcp_server_fn: Any = None,
    ) -> None:
        self._plugin_status_fn = plugin_status_fn
        self._mcp_server_fn = mcp_server_fn

    @property
    def name(self) -> str:
        return "plugin_availability"

    def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
        if tool.source == "builtin":
            return PolicyDecision.ALLOW

        if tool.source == "mcp" and tool.mcp_server:
            if self._mcp_server_fn and not self._mcp_server_fn(tool.mcp_server):
                logger.debug("PluginAvailability: MCP server '%s' not registered", tool.mcp_server)
                return PolicyDecision.DENY
            return PolicyDecision.ALLOW

        if tool.plugin_name and self._plugin_status_fn and not self._plugin_status_fn(tool.plugin_name):
            logger.debug("PluginAvailability: plugin '%s' disabled", tool.plugin_name)
            return PolicyDecision.DENY

        return PolicyDecision.ALLOW


class ProfileLayer(PolicyLayer):
    """Layer 1: Per-agent/workspace declarative rules.

    Args:
        rules: List of rule dicts with keys: action, tool_pattern, priority, arg_conditions.
    """

    def __init__(self, rules: list[dict[str, Any]] | None = None) -> None:
        self._rules = rules or []

    @property
    def name(self) -> str:
        return "profile"

    def set_rules(self, rules: list[dict[str, Any]]) -> None:
        self._rules = rules

    def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
        if not self._rules:
            return PolicyDecision.PASSTHROUGH

        for action in ("deny", "ask", "allow"):
            tier = [r for r in self._rules if r.get("action") == action]
            tier.sort(key=lambda r: r.get("priority", 0), reverse=True)
            for rule in tier:
                pattern = rule.get("tool_pattern", "")
                if not fnmatch.fnmatch(tool.name, pattern):
                    continue
                conditions = rule.get("arg_conditions")
                if conditions and not self._match_conditions(context.arguments, conditions):
                    continue
                if action == "deny":
                    return PolicyDecision.DENY
                if action == "ask":
                    return PolicyDecision.REQUIRE_APPROVAL
                return PolicyDecision.ALLOW

        return PolicyDecision.PASSTHROUGH

    def _match_conditions(
        self,
        arguments: dict[str, Any],
        conditions: dict[str, str],
    ) -> bool:
        for key, pattern in conditions.items():
            value = arguments.get(key)
            if value is None:
                return False
            if not fnmatch.fnmatch(str(value), pattern):
                return False
        return True


class VisibilityLayer(PolicyLayer):
    """Layer 2: available_when expression evaluation.

    Wraps the existing ToolGateEvaluator. Returns HIDE if the expression
    evaluates to falsy (during visibility filtering) or ALLOW otherwise.

    Args:
        evaluator: ToolGateEvaluator instance.
    """

    def __init__(self, evaluator: ToolGateEvaluator | None = None) -> None:
        self._evaluator = evaluator or ToolGateEvaluator()

    @property
    def name(self) -> str:
        return "visibility"

    def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
        if tool.available_when is None:
            return PolicyDecision.ALLOW

        eval_context: dict[str, Any] = {}
        eval_context.update(context.execution_context)
        eval_context.update(context.channel_snapshot)

        if not self._evaluator.evaluate(tool.available_when, eval_context):
            return PolicyDecision.HIDE

        return PolicyDecision.ALLOW


class SecurityLayer(PolicyLayer):
    """Layer 3: Wraps existing ToolAccessPolicy.

    Maps AccessDecision to PolicyDecision. Internal ToolAccessPolicy logic
    is unchanged — this layer only adapts the interface.

    Args:
        policy: ToolAccessPolicy instance.
    """

    def __init__(self, policy: ToolAccessPolicy | None = None) -> None:
        self._policy = policy or ToolAccessPolicy()

    @property
    def name(self) -> str:
        return "security"

    def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
        tool_meta: dict[str, Any] = {
            "name": tool.name,
            "risk_level": tool.risk_level,
            "approval_required": tool.approval_required,
            "sandbox_enabled": tool.sandbox_enabled,
        }
        eval_context: dict[str, Any] = {"tool_name": tool.name}
        if context.workspace_root:
            eval_context["workspace_root"] = context.workspace_root

        access_decision = self._policy.evaluate(
            tool_meta=tool_meta,
            rules=context.rules,
            context=eval_context,
            arguments=context.arguments,
        )

        mapping = {
            AccessDecision.EXECUTE: PolicyDecision.ALLOW,
            AccessDecision.EXECUTE_SANDBOX: PolicyDecision.EXECUTE_SANDBOX,
            AccessDecision.REQUIRE_APPROVAL: PolicyDecision.REQUIRE_APPROVAL,
            AccessDecision.DENY: PolicyDecision.DENY,
        }
        return mapping.get(access_decision, PolicyDecision.ALLOW)


class ModeLayer(PolicyLayer):
    """Layer 4: PermissionMode override.

    Args:
        mode: Current PermissionMode.
        allowlist: Set of tool names allowed in RESTRICTED mode.
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        allowlist: list[str] | None = None,
    ) -> None:
        self._mode = mode
        self._allowlist = set(allowlist or [])

    @property
    def name(self) -> str:
        return "mode"

    def set_mode(self, mode: PermissionMode, allowlist: list[str] | None = None) -> None:
        self._mode = mode
        if allowlist is not None:
            self._allowlist = set(allowlist)

    def evaluate(self, tool: ToolInfo, context: PolicyContext) -> PolicyDecision:
        if self._mode == PermissionMode.RESTRICTED and tool.name not in self._allowlist:
            logger.debug("ModeLayer RESTRICTED: tool '%s' not in allowlist", tool.name)
            return PolicyDecision.DENY

        return PolicyDecision.PASSTHROUGH

    def override_decision(
        self,
        decision: PolicyDecision,
        tool_name: str,
    ) -> PolicyDecision:
        """Apply AUDIT mode override to a final decision.

        In AUDIT mode, DENY is overridden to ALLOW with a WARNING log.
        REQUIRE_APPROVAL is preserved (dangerous operations still need approval).
        """
        if self._mode != PermissionMode.AUDIT:
            return decision

        if decision == PolicyDecision.DENY:
            logger.warning("AUDIT mode: overriding DENY to ALLOW for tool '%s'", tool_name)
            return PolicyDecision.ALLOW

        return decision
