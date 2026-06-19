"""Tool execution security — access policy, risk levels, and approval callback.

Implements three-layer tool execution security:
1. Rule engine — allow/deny/ask pattern matching (Claude Code style)
2. Risk-level policy — default enforcement per LOW/MEDIUM/HIGH/CRITICAL
3. Sandbox routing — integration with DockerSandboxExecutor

Design decisions (see openspec/changes/execution-security/design.md):
- D24: RiskLevel as StrEnum, storage remains String for backward compat
- D25: Three-layer evaluation (rules → risk level → sandbox)
- D26: ToolAccessPolicy in engine layer (zero external dependencies)
- D27: ApprovalCallback blocking pattern (not Command.interrupt)
- D28: Two-layer rule storage (workspace ToolPolicyModel + agent guardrail_config)
- D29: Fail-closed timeout (deny on timeout)
- D30: ApprovalScope caching (ONCE/SESSION/PROJECT/GLOBAL)
"""

from __future__ import annotations

import fnmatch
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class RiskLevel(StrEnum):
    """Qualitative risk classification for tool execution enforcement."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AccessDecision(StrEnum):
    """Outcome of ToolAccessPolicy.evaluate() — what to do with a tool call."""

    EXECUTE = "execute"
    EXECUTE_SANDBOX = "execute_sandbox"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class ApprovalScope(StrEnum):
    """How long an approval decision remains valid."""

    ONCE = "once"
    SESSION = "session"
    PROJECT = "project"
    GLOBAL = "global"


class RuleAction(StrEnum):
    """Rule engine actions for per-tool pattern matching."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ApprovalDecision:
    """Result of an approval request.

    Attributes:
        approved: Whether the tool call is approved for execution.
        reason: Human-readable explanation of the decision.
        scope: How long this approval remains valid.
    """

    approved: bool
    reason: str = ""
    scope: ApprovalScope = ApprovalScope.ONCE


@dataclass
class ToolRule:
    """A single rule in the tool access rule engine.

    Attributes:
        action: What to do when the pattern matches (ALLOW/DENY/ASK).
        pattern: Glob pattern for tool name matching (e.g. ``"terminal(git:*)"``).
        priority: Sort order within the same action (higher = checked first).
    """

    action: RuleAction
    pattern: str
    priority: int = 0


class ApprovalCallback(ABC):
    """Abstract interface for blocking tool approval requests.

    Implementations (in services layer) persist approval records, push
    notifications, and await external decisions with a configurable timeout.
    Timeout or missing callback → deny (fail-closed).
    """

    @abstractmethod
    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk_level: str,
        context: dict[str, Any],
    ) -> ApprovalDecision:
        """Request approval for a tool call, blocking until a decision arrives.

        Args:
            tool_name: Name of the tool requesting approval.
            arguments: Tool call arguments that need approval.
            risk_level: Risk level of the tool (low/medium/high/critical).
            context: Runtime context (session_id, user_id, etc.).

        Returns:
            ApprovalDecision with approved=True/False and scope.
        """
        ...


class ToolAccessPolicy:
    """Three-layer tool execution security evaluator.

    Layer 1 — Rule Engine: deny/ask/allow rules with glob pattern matching.
    Layer 2 — Risk Level: default enforcement per LOW/MEDIUM/HIGH/CRITICAL.
    Layer 3 — Sandbox Routing: sandbox_enabled tools route to sandbox executor.

    This class is a pure evaluator — it does not query the database or call
    services. Rule data is passed in by the caller (ToolWorker).
    """

    def evaluate(
        self,
        tool_meta: dict[str, Any],
        rules: list[ToolRule],
        context: dict[str, Any],
    ) -> AccessDecision:
        """Evaluate tool access policy and return an access decision.

        Args:
            tool_meta: Tool metadata dict with keys: risk_level (str),
                approval_required (bool), sandbox_enabled (bool).
            rules: Ordered list of ToolRule instances from workspace and
                agent-level configurations.
            context: Runtime context dict, may contain tool_name.

        Returns:
            AccessDecision indicating what to do with the tool call.
        """
        tool_name = context.get("tool_name", "") or tool_meta.get("name", "")

        rule_action = self._match_rules(tool_name, rules)
        if rule_action == RuleAction.DENY:
            return AccessDecision.DENY
        if rule_action == RuleAction.ASK:
            return AccessDecision.REQUIRE_APPROVAL

        sandbox_enabled = tool_meta.get("sandbox_enabled", False)
        if rule_action == RuleAction.ALLOW:
            return AccessDecision.EXECUTE_SANDBOX if sandbox_enabled else AccessDecision.EXECUTE

        if tool_meta.get("approval_required", False):
            return AccessDecision.REQUIRE_APPROVAL

        return self._risk_level_fallback(tool_meta, sandbox_enabled)

    def _match_rules(
        self,
        tool_name: str,
        rules: list[ToolRule],
    ) -> RuleAction | None:
        """Match tool name against rules, returning the winning action.

        Evaluation order: DENY first, then ASK, then ALLOW.
        Within each tier, higher priority rules are checked first.

        Args:
            tool_name: Tool name to match.
            rules: List of ToolRule instances.

        Returns:
            The winning RuleAction, or None if no rule matched.
        """
        for action in (RuleAction.DENY, RuleAction.ASK, RuleAction.ALLOW):
            tier = [r for r in rules if r.action == action]
            tier.sort(key=lambda r: r.priority, reverse=True)
            for rule in tier:
                if fnmatch.fnmatch(tool_name, rule.pattern):
                    return action
        return None

    def _risk_level_fallback(
        self,
        tool_meta: dict[str, Any],
        sandbox_enabled: bool,
    ) -> AccessDecision:
        """Determine access decision based on risk level when no rule matched.

        Args:
            tool_meta: Tool metadata dict.
            sandbox_enabled: Whether the tool has sandbox execution enabled.

        Returns:
            AccessDecision based on risk level semantics.
        """
        risk_level = str(tool_meta.get("risk_level", "low")).lower()

        if risk_level == RiskLevel.CRITICAL.value:
            return AccessDecision.REQUIRE_APPROVAL

        if risk_level == RiskLevel.HIGH.value:
            if sandbox_enabled:
                return AccessDecision.EXECUTE_SANDBOX
            return AccessDecision.REQUIRE_APPROVAL

        if risk_level in (RiskLevel.LOW.value, RiskLevel.MEDIUM.value):
            if sandbox_enabled:
                return AccessDecision.EXECUTE_SANDBOX
            return AccessDecision.EXECUTE

        return AccessDecision.EXECUTE_SANDBOX if sandbox_enabled else AccessDecision.EXECUTE
