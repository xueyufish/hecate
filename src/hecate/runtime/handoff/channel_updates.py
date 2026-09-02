"""Engine-side handoff channel-update construction.

Builds the message list that should be written to the ``messages`` channel
when a handoff occurs between agent nodes. Lives in ``engine/`` (not
``services/orchestration/``) because ``AgentWorker`` is the only consumer
and engine/ must not depend on services/.

Pure data transforms — no I/O, no port calls, no DB. Three handoff context
strategies are supported:

- ``inherited`` (default): full message history passed through.
- ``isolated``: fresh context with only the triggering message.
- ``summarized``: collapsed summary of prior history.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


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
