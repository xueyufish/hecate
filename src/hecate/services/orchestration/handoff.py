"""Handoff tool provider for multi-agent orchestration.

Generates the ``handoff_to_agent`` tool schema and injects it into agent
tool lists based on handoff edges in the compiled graph. When the LLM
calls this tool, the execution returns a Command(goto=target) to transfer
control to the target agent node.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.engine.types import Command, CompiledGraph, WorkerResult

logger = logging.getLogger(__name__)


def build_handoff_tool_schema(target_node_ids: list[str]) -> dict[str, Any]:
    """Build the JSON schema for the handoff_to_agent tool.

    The tool accepts a ``target`` parameter specifying which agent to
    hand off control to. Only node IDs that are connected via handoff
    edges are valid targets.

    Args:
        target_node_ids: List of valid target agent node IDs.

    Returns:
        Tool schema dict suitable for inclusion in an LLM tool list.
    """
    return {
        "type": "function",
        "function": {
            "name": "handoff_to_agent",
            "description": (
                "Transfer control to another agent. Use this when the user's "
                "request is better handled by a specialist agent. "
                f"Available targets: {', '.join(target_node_ids)}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": target_node_ids,
                        "description": "The agent node ID to transfer control to",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why this handoff is needed",
                    },
                },
                "required": ["target"],
            },
        },
    }


def get_handoff_targets_for_node(
    compiled: CompiledGraph,
    node_id: str,
) -> list[str]:
    """Return agent node IDs that are handoff targets from the given node.

    Scans the compiled graph's edges for handoff-trigger edges originating
    from ``node_id`` and returns the target node IDs.

    Args:
        compiled: The compiled graph to scan.
        node_id: The source node to find handoff targets for.

    Returns:
        List of target agent node IDs reachable via handoff edges.
    """
    targets: list[str] = []
    for edge in compiled.edges:
        if edge.source == node_id and edge.trigger in ("handoff", "dynamic_handoff"):
            if isinstance(edge.target, str):
                targets.append(edge.target)
            elif isinstance(edge.target, dict):
                targets.extend(edge.target.values())
    return targets


def inject_handoff_tools(
    tools: list[dict[str, Any]],
    compiled: CompiledGraph,
    node_id: str,
) -> list[dict[str, Any]]:
    """Inject the handoff tool into an agent's tool list if handoff edges exist.

    If the node has handoff edges in the compiled graph, the ``handoff_to_agent``
    tool is appended to the tool list. Otherwise, the tool list is returned
    unchanged.

    Args:
        tools: The agent's current tool list.
        compiled: The compiled graph containing edge definitions.
        node_id: The agent node whose tools are being prepared.

    Returns:
        Updated tool list with handoff tool appended if applicable.
    """
    targets = get_handoff_targets_for_node(compiled, node_id)
    if not targets:
        return tools
    handoff_tool = build_handoff_tool_schema(targets)
    return list(tools) + [handoff_tool]


def validate_handoff_target(
    compiled: CompiledGraph,
    node_id: str,
    target: str,
) -> str:
    """Validate that a handoff target is in the allowed candidate list.

    Checks the compiled graph edges for handoff and dynamic_handoff edges
    originating from ``node_id`` and ensures ``target`` is among the valid
    candidates. Raises ``ValueError`` if the target is not allowed.

    Args:
        compiled: The compiled graph containing edge definitions.
        node_id: The source node attempting the handoff.
        target: The proposed target node ID.

    Returns:
        The validated target string.

    Raises:
        ValueError: If the target is not a valid handoff destination.
    """
    allowed = get_handoff_targets_for_node(compiled, node_id)
    if target not in allowed:
        raise ValueError(
            f"Invalid handoff target '{target}' from node '{node_id}'. "
            f"Allowed targets: {', '.join(allowed) or '(none)'}"
        )
    return target


def is_handoff_tool_call(tool_name: str) -> bool:
    """Check if a tool call name is the handoff tool.

    Args:
        tool_name: The tool name from the LLM's tool call.

    Returns:
        True if this is the handoff tool.
    """
    return tool_name == "handoff_to_agent"


def create_handoff_worker_result(
    node_id: str,
    target: str,
) -> WorkerResult:
    """Create a WorkerResult that triggers a handoff via Command(goto).

    When the LLM calls the handoff tool, this function creates the
    appropriate WorkerResult with a Command directing execution to the
    target agent node.

    Args:
        node_id: The current agent node that initiated the handoff.
        target: The target agent node ID to hand off to.

    Returns:
        WorkerResult with Command(goto=target).
    """
    return WorkerResult(
        node_id=node_id,
        channel_updates={
            "messages": [
                {"role": "assistant", "content": f"Transferring to {target}..."},
            ],
        },
        command=Command(goto=target),
    )
