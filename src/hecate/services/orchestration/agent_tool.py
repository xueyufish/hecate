"""Agent-as-Tool provider for multi-agent orchestration.

Exposes target agents as callable tools when the AGENT node is configured
with ``invocation_mode: "tool"``. This enables hierarchical delegation
where a parent LLM can invoke a specialist agent via tool calling.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel

logger = logging.getLogger(__name__)


def build_agent_tool_schema(agent: AgentModel) -> dict[str, Any]:
    """Build a tool schema that represents an agent as a callable tool.

    The tool is named ``agent_{sanitized_name}`` and described using the
    agent's persona. It accepts a single ``task`` parameter.

    Args:
        agent: The AgentModel to expose as a tool.

    Returns:
        Tool schema dict suitable for inclusion in an LLM tool list.
    """
    sanitized = agent.name.lower().replace(" ", "_").replace("-", "_")
    return {
        "type": "function",
        "function": {
            "name": f"agent_{sanitized}",
            "description": f"Delegate to {agent.name}: {agent.persona or 'A specialist agent'}",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": f"The task to delegate to {agent.name}",
                    },
                },
                "required": ["task"],
            },
        },
        "_agent_id": str(agent.id),
    }


def is_agent_tool(tool_name: str) -> bool:
    """Check if a tool name matches the agent tool naming pattern.

    Args:
        tool_name: The tool name to check.

    Returns:
        True if the name starts with ``agent_`` prefix.
    """
    return tool_name.startswith("agent_") and tool_name != "agent"


async def load_agent_tool_schemas(
    db: AsyncSession,
    agent_ids: list[str],
) -> list[dict[str, Any]]:
    """Load tool schemas for a list of agent IDs.

    Resolves each agent_id to an AgentModel and generates the tool schema.
    Skips IDs that don't resolve to valid agents.

    Args:
        db: Database session for agent lookups.
        agent_ids: List of agent ID strings to expose as tools.

    Returns:
        List of tool schema dicts.
    """
    schemas: list[dict[str, Any]] = []
    for agent_id_str in agent_ids:
        try:
            agent_id = UUID(agent_id_str)
        except ValueError:
            logger.warning("Invalid agent ID in tool config: %s", agent_id_str)
            continue

        result = await db.execute(
            select(AgentModel).where(
                AgentModel.id == agent_id,
                ~AgentModel.deleted,
            )
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            logger.warning("Agent not found for tool schema: %s", agent_id_str)
            continue
        schemas.append(build_agent_tool_schema(agent))
    return schemas


def find_agent_id_for_tool(
    tool_name: str,
    tools: list[dict[str, Any]],
) -> str | None:
    """Find the agent_id associated with a tool name.

    Searches the tool list for an agent tool matching the given name
    and returns its ``_agent_id`` metadata field.

    Args:
        tool_name: The tool name from the LLM's tool call.
        tools: The tool list containing agent tool schemas.

    Returns:
        The agent ID string if found, None otherwise.
    """
    for tool in tools:
        func = tool.get("function", {})
        if func.get("name") == tool_name:
            return tool.get("_agent_id")
    return None
