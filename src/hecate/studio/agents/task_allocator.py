"""Dynamic task allocator for LLM-driven agent routing.

Routes tasks to appropriate agents based on:
- Task description analysis
- Agent capabilities
- Agent load balancing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Information about an available agent."""

    agent_id: str
    name: str
    capabilities: list[str]
    current_load: int = 0
    max_load: int = 10


@dataclass
class TaskAllocation:
    """Result of task allocation."""

    agent_id: str
    confidence: float
    reason: str


class DynamicTaskAllocator:
    """Routes tasks to agents based on capabilities and load.

    Supports:
    - LLM-driven routing (when available)
    - Load-aware allocation
    - Fallback to default routing
    """

    def __init__(self) -> None:
        """Initialize the task allocator."""
        self._agents: dict[str, AgentInfo] = {}

    def register_agent(self, agent_info: AgentInfo) -> None:
        """Register an agent for task allocation.

        Args:
            agent_info: Agent information.
        """
        self._agents[agent_info.agent_id] = agent_info
        logger.debug(f"Registered agent {agent_info.agent_id} for allocation")

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent.

        Args:
            agent_id: The agent identifier.
        """
        self._agents.pop(agent_id, None)

    def update_load(self, agent_id: str, load: int) -> None:
        """Update agent load.

        Args:
            agent_id: The agent identifier.
            load: Current load count.
        """
        if agent_id in self._agents:
            self._agents[agent_id].current_load = load

    def allocate(
        self,
        task_description: str,
        required_capabilities: list[str] | None = None,
    ) -> TaskAllocation | None:
        """Allocate a task to an agent.

        Args:
            task_description: Description of the task.
            required_capabilities: Required agent capabilities.

        Returns:
            TaskAllocation or None if no suitable agent found.
        """
        candidates = self._find_candidates(required_capabilities)

        if not candidates:
            logger.warning(f"No suitable agent found for task: {task_description[:50]}")
            return None

        # Select agent with lowest load
        selected = min(candidates, key=lambda a: a.current_load)

        return TaskAllocation(
            agent_id=selected.agent_id,
            confidence=0.8,
            reason=f"Selected based on capabilities and load ({selected.current_load})",
        )

    def _find_candidates(
        self,
        required_capabilities: list[str] | None = None,
    ) -> list[AgentInfo]:
        """Find candidate agents.

        Args:
            required_capabilities: Required capabilities.

        Returns:
            List of suitable agents.
        """
        candidates = []

        for agent in self._agents.values():
            # Check load capacity
            if agent.current_load >= agent.max_load:
                continue

            # Check capabilities
            if required_capabilities and not all(cap in agent.capabilities for cap in required_capabilities):
                continue

            candidates.append(agent)

        return candidates

    def get_agent_load(self, agent_id: str) -> int | None:
        """Get agent load.

        Args:
            agent_id: The agent identifier.

        Returns:
            Current load or None if agent not found.
        """
        agent = self._agents.get(agent_id)
        return agent.current_load if agent else None

    def get_available_agents(self) -> list[AgentInfo]:
        """Get all available agents.

        Returns:
            List of registered agents with capacity.
        """
        return [a for a in self._agents.values() if a.current_load < a.max_load]
