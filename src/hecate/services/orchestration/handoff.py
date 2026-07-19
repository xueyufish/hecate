"""Handoff tool provider for multi-agent orchestration.

Generates the ``handoff_to_agent`` tool schema and injects it into agent
tool lists based on handoff edges in the compiled graph. When the LLM
calls this tool, the execution returns a Command(goto=target) to transfer
control to the target agent node.

Supports three context-passing strategies for the downstream agent:
- ``inherited`` (default): full message history passed through.
- ``isolated``: fresh context with only the triggering message.
- ``summarized``: collapsed summary of prior history.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hecate.engine.types import Command, CompiledGraph, WorkerResult

logger = logging.getLogger(__name__)


def build_handoff_tool_schema(
    target_node_ids: list[str],
    descriptions_by_target: dict[str, str] | None = None,
    source_description: str | None = None,
) -> dict[str, Any]:
    """Build the JSON schema for the handoff_to_agent tool.

    The tool accepts a ``target`` parameter specifying which agent to
    hand off control to. Only node IDs that are connected via handoff
    edges are valid targets.

    When ``descriptions_by_target`` is provided, the tool description
    includes each target's role for accurate LLM routing. When absent,
    falls back to a generic description.

    Args:
        target_node_ids: List of valid target agent node IDs.
        descriptions_by_target: Optional dict mapping target node ID to
            its role description. Used to build per-target descriptions.
        source_description: Optional override for the overall tool description.
            When set, replaces the auto-generated description entirely.

    Returns:
        Tool schema dict suitable for inclusion in an LLM tool list.
    """
    if source_description:
        tool_description = source_description
    elif descriptions_by_target:
        lines = ["Transfer to a specialist agent. Available targets:"]
        for target_id in target_node_ids:
            desc = descriptions_by_target.get(target_id, target_id)
            lines.append(f"- {target_id}: {desc}")
        tool_description = "\n".join(lines)
    else:
        tool_description = (
            "Transfer control to another agent. Use this when the user's "
            "request is better handled by a specialist agent. "
            f"Available targets: {', '.join(target_node_ids)}"
        )

    return {
        "type": "function",
        "function": {
            "name": "handoff_to_agent",
            "description": tool_description,
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


def inject_handoff_tools_from_targets(
    tools: list[dict[str, Any]],
    handoff_targets: list[dict[str, str]],
    source_description: str | None = None,
) -> list[dict[str, Any]]:
    """Inject the handoff tool using a pre-built target list.

    Unlike ``inject_handoff_tools``, this function does not require a
    CompiledGraph. It accepts a list of ``{"node_id": str, "description": str}``
    dicts as populated by ``PregelRuntime._build_handoff_targets``.

    Args:
        tools: The agent's current tool list.
        handoff_targets: List of target dicts from execution_context.
        source_description: Optional override for the tool description.

    Returns:
        Updated tool list with handoff tool appended.
    """
    if not handoff_targets:
        return tools
    target_ids = [t["node_id"] for t in handoff_targets]
    descriptions = {t["node_id"]: t.get("description", t["node_id"]) for t in handoff_targets}
    handoff_tool = build_handoff_tool_schema(
        target_ids,
        descriptions_by_target=descriptions,
        source_description=source_description,
    )
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


def validate_handoff_target_from_list(
    handoff_targets: list[dict[str, str]],
    target: str,
) -> str:
    """Validate that a handoff target is in the allowed target list.

    Args:
        handoff_targets: List of target dicts from execution_context.
        target: The proposed target node ID.

    Returns:
        The validated target string.

    Raises:
        ValueError: If the target is not a valid handoff destination.
    """
    allowed = {t["node_id"] for t in handoff_targets}
    if target not in allowed:
        raise ValueError(
            f"Invalid handoff target '{target}'. Allowed targets: {', '.join(sorted(allowed)) or '(none)'}"
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


def filter_messages_for_handoff(
    messages: list[dict[str, Any]],
    context_mode: str,
    source_node_id: str,
    target_node_id: str,
) -> list[dict[str, Any]]:
    """Filter conversation history for handoff based on context_mode.

    This is the public entry point for context-mode-aware message filtering.
    It produces the message list that the downstream agent will see, BEFORE
    the AIMessage + ToolMessage pair is appended.

    Args:
        messages: The full message history at handoff time.
        context_mode: One of "inherited", "isolated", "summarized".
        source_node_id: The node that initiated the handoff.
        target_node_id: The node being handed off to.

    Returns:
        Filtered message list according to context_mode.

    Raises:
        ValueError: If context_mode is not one of the three valid values.
    """
    if context_mode == "inherited":
        return list(messages)
    elif context_mode == "isolated":
        return [{"role": "system", "content": f"Handed off from {source_node_id}"}]
    elif context_mode == "summarized":
        summary = _build_structured_summary(messages, source_node_id)
        return [{"role": "system", "content": summary}]
    else:
        raise ValueError(f"Invalid context_mode '{context_mode}'. Must be 'inherited', 'isolated', or 'summarized'.")


def build_handoff_channel_updates(
    messages_snapshot: list[dict[str, Any]],
    source_node_id: str,
    target_node_id: str,
    context_mode: str,
    tool_call_id: str,
    llm_tool_call_message: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the correctly-paired messages list for a handoff channel update.

    This function produces the complete message list that should be written
    to the ``messages`` channel when a handoff occurs. It includes:
    1. Filtered history according to context_mode.
    2. The AIMessage containing the tool call (paired).
    3. A synthetic ToolMessage acknowledging the handoff (paired).

    The AIMessage + ToolMessage pairing ensures downstream LLMs see a
    well-formed conversation (LangGraph contract).

    Args:
        messages_snapshot: Full message history at handoff time.
        source_node_id: The node initiating the handoff.
        target_node_id: The target node for the handoff.
        context_mode: One of "inherited", "isolated", "summarized".
        tool_call_id: The LLM's tool_call_id for the handoff_to_agent call.
        llm_tool_call_message: Optional full AIMessage from the LLM. If None,
            a synthetic AIMessage is created with the tool call.

    Returns:
        Complete message list for channel_updates["messages"].
    """
    # Filter history according to context_mode
    filtered = filter_messages_for_handoff(messages_snapshot, context_mode, source_node_id, target_node_id)

    # Deduplicate tool_call_ids to prevent collisions
    tool_call_id = _deduplicate_tool_call_id(tool_call_id, filtered)

    # Build the AIMessage with tool call
    if llm_tool_call_message is not None:
        aimessage = dict(llm_tool_call_message)
        # Ensure tool_call_id is preserved exactly
        if "tool_calls" in aimessage:
            for tc in aimessage.get("tool_calls", []):
                if isinstance(tc, dict):
                    tc["id"] = tool_call_id
    else:
        aimessage = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": "handoff_to_agent", "arguments": f'{{"target": "{target_node_id}"}}'},
                }
            ],
        }

    # Build the ToolMessage
    toolmessage = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": f"Handed off to {target_node_id}",
    }

    return filtered + [aimessage, toolmessage]


def _deduplicate_tool_call_id(tool_call_id: str, messages: list[dict[str, Any]]) -> str:
    """Ensure tool_call_id is unique by appending UUID suffix on collision.

    If the same tool_call_id already appears in the message history, appends
    a short UUID hex suffix to avoid downstream LLM confusion.

    Args:
        tool_call_id: The original tool_call_id from the LLM.
        messages: The message history to check for collisions.

    Returns:
        The (possibly modified) tool_call_id.
    """
    existing_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict):
                    existing_ids.add(tc.get("id", ""))
        if msg.get("role") == "tool":
            existing_ids.add(msg.get("tool_call_id", ""))

    if tool_call_id not in existing_ids:
        return tool_call_id

    suffix = uuid.uuid4().hex[:8]
    new_id = f"{tool_call_id}-{suffix}"
    logger.warning(
        "tool_call_id collision detected: '%s' already in history. Using '%s' instead.",
        tool_call_id,
        new_id,
    )
    return new_id


def _build_structured_summary(messages: list[dict[str, Any]], source_node_id: str) -> str:
    """Build a structured summary of the message history for summarized mode.

    This is a deterministic fallback that extracts key information without
    requiring an LLM call. For production use, the port can override this
    with an LLM-generated summary.

    Args:
        messages: The full message history.
        source_node_id: The node that initiated the handoff.

    Returns:
        A structured summary string.
    """
    if not messages:
        return f"Handed off from {source_node_id}. No prior conversation."

    # Extract the last user message as the intent
    user_messages = [m for m in messages if m.get("role") == "user"]
    last_user = user_messages[-1].get("content", "") if user_messages else ""

    # Extract assistant messages for key facts
    assistant_messages = [m for m in messages if m.get("role") == "assistant"]
    last_assistant = assistant_messages[-1].get("content", "") if assistant_messages else ""

    parts = [f"Handed off from {source_node_id}."]
    if last_user:
        parts.append(f"User intent: {last_user[:200]}")
    if last_assistant:
        parts.append(f"Last response: {last_assistant[:200]}")
    parts.append(f"Message count: {len(messages)}")

    return "\n".join(parts)
