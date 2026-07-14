"""Composable tool policy pipeline.

Provides the core pipeline abstraction for evaluating tool access through
ordered, pluggable policy layers. Each layer receives ``(tool, context)``
and returns a :class:`PolicyDecision`. DENY short-circuits the pipeline.

Design decisions (see openspec/changes/tool-permission-control/design.md):
- 5 layers in fixed order: PluginAvailability → Profile → Visibility → Security → Mode
- DENY short-circuits; HIDE only affects visibility filtering
- Two interception points: visibility (LLM context) and execution-time
- Existing ToolAccessPolicy and ToolGateEvaluator are wrapped as layers (zero rewrite)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class PolicyDecision(StrEnum):
    """Pipeline decision for a tool call."""

    ALLOW = "allow"
    DENY = "deny"
    HIDE = "hide"
    REQUIRE_APPROVAL = "require_approval"
    EXECUTE_SANDBOX = "execute_sandbox"
    PASSTHROUGH = "passthrough"


class PermissionMode(StrEnum):
    """Global permission mode per agent."""

    DEFAULT = "default"
    RESTRICTED = "restricted"
    AUDIT = "audit"


@dataclass
class ToolInfo:
    """Information about a tool being evaluated."""

    name: str
    source: str = "builtin"
    risk_level: str = "low"
    approval_required: bool = False
    sandbox_enabled: bool = False
    available_when: str | None = None
    plugin_name: str | None = None
    mcp_server: str | None = None
    tool_def: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyContext:
    """Runtime context for policy evaluation."""

    agent_id: str | None = None
    workspace_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    channel_snapshot: dict[str, Any] = field(default_factory=dict)
    execution_context: dict[str, Any] = field(default_factory=dict)
    rules: list[Any] = field(default_factory=list)
    workspace_root: str | None = None


@dataclass
class LayerResult:
    """Result of a single layer evaluation."""

    layer_name: str
    decision: PolicyDecision
    reason: str = ""


class PolicyLayer(ABC):
    """Abstract base class for composable policy layers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Layer name for logging and audit."""

    @abstractmethod
    def evaluate(
        self,
        tool: ToolInfo,
        context: PolicyContext,
    ) -> PolicyDecision:
        """Evaluate this layer for the given tool and context.

        Args:
            tool: Tool being evaluated.
            context: Runtime context.

        Returns:
            PolicyDecision for this layer.
        """


class ToolPolicyPipeline:
    """Pipeline that evaluates tool access through ordered layers.

    Args:
        layers: Ordered list of PolicyLayer instances.
    """

    def __init__(self, layers: list[PolicyLayer] | None = None) -> None:
        self._layers = layers or []

    @property
    def layers(self) -> list[PolicyLayer]:
        """Configured pipeline layers."""
        return self._layers

    def evaluate_visibility(
        self,
        tools: list[dict[str, Any]],
        context: PolicyContext,
    ) -> list[dict[str, Any]]:
        """Filter tool list for LLM visibility.

        Runs layers that can produce HIDE decisions (PluginAvailability,
        Profile, Visibility). Tools that get HIDE or DENY are removed.

        Args:
            tools: List of tool definition dicts.
            context: Runtime context.

        Returns:
            Filtered tool list.
        """
        visible: list[dict[str, Any]] = []
        for tool_def in tools:
            tool = _tool_def_to_info(tool_def)
            hidden = False
            for layer in self._layers:
                decision = layer.evaluate(tool, context)
                if decision in (PolicyDecision.HIDE, PolicyDecision.DENY):
                    logger.debug(
                        "Visibility: layer '%s' hid/denied tool '%s'",
                        layer.name,
                        tool.name,
                    )
                    hidden = True
                    break
            if not hidden:
                visible.append(tool_def)
        return visible

    def evaluate_execution(
        self,
        tool: ToolInfo,
        context: PolicyContext,
    ) -> tuple[PolicyDecision, list[LayerResult]]:
        """Evaluate all layers for execution-time access decision.

        Args:
            tool: Tool being evaluated.
            context: Runtime context.

        Returns:
            Tuple of (final decision, per-layer results for audit).
        """
        results: list[LayerResult] = []
        final_decision = PolicyDecision.ALLOW

        for layer in self._layers:
            decision = layer.evaluate(tool, context)
            results.append(LayerResult(layer_name=layer.name, decision=decision))

            if decision == PolicyDecision.DENY:
                final_decision = PolicyDecision.DENY
                break
            if decision in (
                PolicyDecision.REQUIRE_APPROVAL,
                PolicyDecision.EXECUTE_SANDBOX,
            ):
                final_decision = decision

        logger.debug(
            "Pipeline evaluated tool '%s': final=%s, layers=%s",
            tool.name,
            final_decision,
            [(r.layer_name, r.decision.value) for r in results],
        )

        return final_decision, results


def _tool_def_to_info(tool_def: dict[str, Any]) -> ToolInfo:
    """Convert a tool definition dict to ToolInfo."""
    return ToolInfo(
        name=tool_def.get("name", ""),
        source=tool_def.get("source", "builtin"),
        risk_level=tool_def.get("risk_level", "low"),
        approval_required=tool_def.get("approval_required", False),
        sandbox_enabled=tool_def.get("sandbox_enabled", False),
        available_when=tool_def.get("available_when"),
        plugin_name=tool_def.get("plugin_name"),
        mcp_server=tool_def.get("mcp_server"),
        tool_def=tool_def,
    )
