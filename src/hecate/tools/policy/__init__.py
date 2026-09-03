"""Tool policy pipeline — composable evaluation layers in fixed order.

Pipeline ordering (evaluation halts on ``DENY``):

1. PluginAvailability — plugin / MCP server enabled?
2. Profile — per-agent / workspace declarative rules (glob + arg conditions)
3. Visibility — ``available_when`` expression evaluation
4. Security — wraps existing :class:`ToolAccessPolicy` (5-layer security eval)
5. Mode — PermissionMode override (DEFAULT / RESTRICTED / AUDIT)

Lives in the tools domain (was misplaced under ``services/observability``
before PR phase-r-domain-reorg-followups). All consumers come from
:mod:`hecate.runtime.tool_access` and :mod:`hecate.runtime.tool_gate`;
the pipeline is a pure-data abstraction over tool policy.

This package exposes the pipeline definition. The five concrete layer
implementations live in :mod:`hecate.tools.policy.policy_layers` and
are imported on demand to avoid a circular dependency (``policy_layers``
references the pipeline's data types, not the other way round).
"""

from __future__ import annotations

from .policy_pipeline import (
    PermissionMode,
    PolicyContext,
    PolicyDecision,
    ToolInfo,
    ToolPolicyPipeline,
)

__all__ = [
    "PermissionMode",
    "PolicyContext",
    "PolicyDecision",
    "ToolInfo",
    "ToolPolicyPipeline",
]
