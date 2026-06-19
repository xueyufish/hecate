"""Tool execution security — access policy, risk levels, and approval callback.

Implements five-layer tool execution security:
1. Dangerous patterns — built-in deny rules (bypass-immune)
2. Rule engine — allow/deny/ask pattern matching with argument conditions
3. Workspace boundary — auto-allow inside workspace, ask outside
4. Risk-level policy — default enforcement per LOW/MEDIUM/HIGH/CRITICAL
5. Sandbox routing — integration with DockerSandboxExecutor

Design decisions:
- D24-D30: See openspec/changes/execution-security/design.md
- D31-D35: See openspec/changes/granular-tool-security/design.md
"""

from __future__ import annotations

import fnmatch
import logging
import os.path
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


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
class DangerousPattern:
    """A built-in dangerous pattern that cannot be overridden by user rules.

    Attributes:
        tool_pattern: Glob pattern for tool name (e.g. ``"*"`` for all tools).
        arg_key: Argument key to inspect (e.g. ``"command"``, ``"path"``).
        arg_pattern: Glob pattern for argument value.
        description: Human-readable reason for blocking.
    """

    tool_pattern: str
    arg_key: str
    arg_pattern: str
    description: str


@dataclass
class ToolRule:
    """A single rule in the tool access rule engine.

    Attributes:
        action: What to do when the pattern matches (ALLOW/DENY/ASK).
        pattern: Glob pattern for tool name matching (e.g. ``"terminal(git:*)"``).
        priority: Sort order within the same action (higher = checked first).
        arg_conditions: Optional dict mapping argument keys to glob patterns.
            When set, the rule matches only if the tool name matches AND all
            argument conditions match their corresponding argument values.
    """

    action: RuleAction
    pattern: str
    priority: int = 0
    arg_conditions: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Built-in dangerous patterns (D32)
# ---------------------------------------------------------------------------

DANGEROUS_PATTERNS: list[DangerousPattern] = [
    # Shell commands — destructive operations
    DangerousPattern("bash", "command", "rm -rf /", "recursive root delete"),
    DangerousPattern("bash", "command", "mkfs*", "filesystem format"),
    DangerousPattern("bash", "command", "dd if=*of=/dev/", "disk overwrite"),
    DangerousPattern("bash", "command", "*curl*|*sh", "remote code execution"),
    DangerousPattern("bash", "command", ":()*{*}*", "fork bomb"),
    DangerousPattern("bash", "command", "rm -rf /*", "recursive root delete"),
    # Code execution — dangerous builtins
    DangerousPattern("execute_code", "code", "*os.system*", "OS system call"),
    DangerousPattern("execute_code", "code", "*subprocess*", "subprocess invocation"),
    DangerousPattern("execute_code", "code", "*eval(*", "eval execution"),
    DangerousPattern("execute_code", "code", "*exec(*", "exec execution"),
    DangerousPattern("execute_code", "code", "*__import__*", "dynamic import"),
    # Sensitive file writes
    DangerousPattern("write_file", "path", "*/.ssh/*", "SSH key write"),
    DangerousPattern("write_file", "path", "*/.env*", "env file write"),
    DangerousPattern("write_file", "path", "*/.bashrc", "shell config write"),
    DangerousPattern("write_file", "path", "/etc/*", "system config write"),
    DangerousPattern("write_file", "path", "*/.git/config", "git config write"),
    # Sensitive file reads
    DangerousPattern("read_file", "path", "/etc/passwd", "password file read"),
    DangerousPattern("read_file", "path", "*/.ssh/id_*", "SSH key read"),
    DangerousPattern("read_file", "path", "/etc/shadow", "shadow file read"),
    # SQL dangerous operations (wildcard tool match)
    DangerousPattern("*", "code", "*DROP TABLE*", "SQL table drop"),
    DangerousPattern("*", "code", "*DELETE FROM*", "SQL delete"),
    DangerousPattern("*", "code", "*TRUNCATE*", "SQL truncate"),
]

# Known path argument keys used by built-in tools (for workspace boundary check)
_PATH_ARG_KEYS: frozenset[str] = frozenset(
    {
        "path",
        "file_path",
        "directory",
        "directory_path",
    }
)


# ---------------------------------------------------------------------------
# ABCs
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# WorkspaceBoundaryPolicy (D33)
# ---------------------------------------------------------------------------


class WorkspaceBoundaryPolicy:
    """Checks if file-path arguments resolve within a workspace root.

    Used as a policy layer between user rules and risk-level fallback.
    Paths inside the workspace are auto-allowed; paths outside require
    approval.
    """

    def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_root: str,
    ) -> AccessDecision | None:
        """Check if tool operates on files within workspace boundary.

        Args:
            tool_name: Name of the tool being called (unused, reserved).
            arguments: Tool call arguments to inspect.
            workspace_root: Absolute path of the workspace directory.

        Returns:
            ``EXECUTE`` if path is inside workspace,
            ``REQUIRE_APPROVAL`` if outside,
            ``None`` if tool has no path argument.
        """
        path_value = self._extract_path(arguments)
        if path_value is None:
            return None

        normalized = self._normalize_path(path_value, workspace_root)
        if normalized.startswith(workspace_root):
            return AccessDecision.EXECUTE
        return AccessDecision.REQUIRE_APPROVAL

    def _extract_path(self, arguments: dict[str, Any]) -> str | None:
        """Extract the first path-like argument value."""
        for key in _PATH_ARG_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _normalize_path(self, path_value: str, workspace_root: str) -> str:
        """Normalize a path, resolving relative paths against workspace root."""
        if os.path.isabs(path_value):
            return os.path.normpath(path_value)
        return os.path.normpath(os.path.join(workspace_root, path_value))


# ---------------------------------------------------------------------------
# ToolAccessPolicy (extended with D31-D35)
# ---------------------------------------------------------------------------


class ToolAccessPolicy:
    """Five-layer tool execution security evaluator.

    Layer 0 — Dangerous Patterns: built-in DENY rules (bypass-immune).
    Layer 1 — Rule Engine: deny/ask/allow rules with arg_conditions matching.
    Layer 2 — Workspace Boundary: auto-allow inside workspace root.
    Layer 3 — Risk Level: default enforcement per LOW/MEDIUM/HIGH/CRITICAL.
    Layer 4 — Sandbox Routing: sandbox_enabled tools route to sandbox executor.

    This class is a pure evaluator — it does not query the database or call
    services. Rule data is passed in by the caller (ToolWorker).
    """

    def __init__(self) -> None:
        self._workspace_policy = WorkspaceBoundaryPolicy()

    def evaluate(
        self,
        tool_meta: dict[str, Any],
        rules: list[ToolRule],
        context: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> AccessDecision:
        """Evaluate tool access policy and return an access decision.

        Args:
            tool_meta: Tool metadata dict with keys: risk_level (str),
                approval_required (bool), sandbox_enabled (bool).
            rules: Ordered list of ToolRule instances from workspace and
                agent-level configurations.
            context: Runtime context dict. May contain ``tool_name`` and
                ``workspace_root``.
            arguments: Parsed tool call arguments. When provided, argument
                conditions on rules and dangerous patterns are checked.
                When ``None``, the method behaves as before (backward
                compatible).

        Returns:
            AccessDecision indicating what to do with the tool call.
        """
        tool_name = context.get("tool_name", "") or tool_meta.get("name", "")

        # Layer 0: Dangerous patterns (bypass-immune, cannot be overridden)
        if arguments and self._match_dangerous_patterns(tool_name, arguments):
            logger.warning("Dangerous pattern matched for tool '%s'", tool_name)
            return AccessDecision.DENY

        # Layer 1: User rules (DENY → ASK → ALLOW with arg_conditions)
        rule_action = self._match_rules(tool_name, rules, arguments)
        if rule_action == RuleAction.DENY:
            return AccessDecision.DENY
        if rule_action == RuleAction.ASK:
            return AccessDecision.REQUIRE_APPROVAL

        sandbox_enabled = tool_meta.get("sandbox_enabled", False)
        if rule_action == RuleAction.ALLOW:
            return AccessDecision.EXECUTE_SANDBOX if sandbox_enabled else AccessDecision.EXECUTE

        # Layer 2: Workspace boundary (if workspace_root in context)
        if arguments:
            workspace_root = context.get("workspace_root")
            if workspace_root:
                boundary = self._workspace_policy.check(tool_name, arguments, workspace_root)
                if boundary is not None:
                    return boundary

        # Layer 3: Risk level fallback
        if tool_meta.get("approval_required", False):
            return AccessDecision.REQUIRE_APPROVAL

        return self._risk_level_fallback(tool_meta, sandbox_enabled)

    def _match_dangerous_patterns(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> bool:
        """Check if any built-in dangerous pattern matches.

        Args:
            tool_name: Name of the tool being called.
            arguments: Parsed tool call arguments.

        Returns:
            True if a dangerous pattern matched.
        """
        for pattern in DANGEROUS_PATTERNS:
            if not fnmatch.fnmatch(tool_name, pattern.tool_pattern):
                continue
            arg_value = arguments.get(pattern.arg_key)
            if arg_value is None:
                continue
            if fnmatch.fnmatch(str(arg_value), pattern.arg_pattern):
                return True
        return False

    def _match_rules(
        self,
        tool_name: str,
        rules: list[ToolRule],
        arguments: dict[str, Any] | None = None,
    ) -> RuleAction | None:
        """Match tool name against rules, returning the winning action.

        Evaluation order: DENY first, then ASK, then ALLOW.
        Within each tier, higher priority rules are checked first.
        If a rule has ``arg_conditions``, all conditions must also match.

        Args:
            tool_name: Tool name to match.
            rules: List of ToolRule instances.
            arguments: Parsed tool call arguments (optional).

        Returns:
            The winning RuleAction, or None if no rule matched.
        """
        for action in (RuleAction.DENY, RuleAction.ASK, RuleAction.ALLOW):
            tier = [r for r in rules if r.action == action]
            tier.sort(key=lambda r: r.priority, reverse=True)
            for rule in tier:
                if not fnmatch.fnmatch(tool_name, rule.pattern):
                    continue
                # Name matches — check arg_conditions if present
                if rule.arg_conditions:
                    if arguments is None:
                        # No arguments available — treat as name-only match
                        # (backward compatible with 9.4 behavior)
                        return action
                    if self._match_arg_conditions(arguments, rule.arg_conditions):
                        return action
                    # arg_conditions present but don't match — skip rule
                    continue
                # No arg_conditions — name-only match
                return action
        return None

    def _match_arg_conditions(
        self,
        arguments: dict[str, Any],
        conditions: dict[str, str],
    ) -> bool:
        """Check if all argument conditions match.

        Args:
            arguments: Tool call arguments dict.
            conditions: Dict of argument key → glob pattern.

        Returns:
            True if ALL conditions match their argument values.
        """
        for key, pattern in conditions.items():
            value = arguments.get(key)
            if value is None:
                return False
            if not fnmatch.fnmatch(str(value), pattern):
                return False
        return True

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
