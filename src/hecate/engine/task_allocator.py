"""Abstract task allocation for multi-agent systems.

Provides the abstract contract (TaskAllocator) and two implementations:
- ``SemanticTaskAllocator`` -- uses LLM to rank candidates by relevance
- ``RoundRobinTaskAllocator`` -- cycles through candidates in order

TaskAllocator selects the best-fit agent from a pool of candidates for a
given task description. P2 supports selection from existing agents only;
dynamic agent creation (create_if_not_found) is reserved for P3.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class TaskAllocator(ABC):
    """Abstract interface for selecting agents for task execution.

    A TaskAllocator examines a task description and a list of candidate
    agents, returning the best-fit agent or None if no suitable candidate
    exists. The ``create_if_not_found`` parameter is reserved for P3
    dynamic agent creation.
    """

    @abstractmethod
    async def allocate(
        self,
        task: str,
        candidates: list[Any],
        create_if_not_found: bool = False,
    ) -> Any | None:
        """Select the best-fit agent for a task.

        Args:
            task: Description of the task to allocate.
            candidates: List of candidate agents (objects with name/description).
            create_if_not_found: Reserved for P3 dynamic agent creation.

        Returns:
            The best-fit candidate, or None if no suitable candidate exists.

        Raises:
            NotImplementedError: If create_if_not_found=True (P3 feature).
        """
        ...


class SemanticTaskAllocator(TaskAllocator):
    """LLM-based task allocation using semantic matching.

    Calls port.llm_invoke() with a ranking prompt that includes the task
    description and all candidate descriptions. Parses the LLM response
    to extract the best-matching agent name.
    """

    def __init__(self, port: Any) -> None:
        """Initialize with an EnginePort-like object for LLM access.

        Args:
            port: An object with an llm_invoke() method returning
                AsyncGenerator[str, None].
        """
        self._port = port

    async def _collect_llm_response(self, prompt: str) -> str:
        """Collect all tokens from llm_invoke into a single string."""
        chunks: list[str] = []
        async for token in self._port.llm_invoke(
            messages=[{"role": "user", "content": prompt}],
            config={},
        ):
            chunks.append(token)
        return "".join(chunks)

    def _build_ranking_prompt(self, task: str, candidates: list[Any]) -> str:
        """Build a prompt asking the LLM to rank candidates for a task."""
        candidate_descriptions = []
        for i, c in enumerate(candidates):
            name = getattr(c, "name", str(c))
            persona = getattr(c, "persona", getattr(c, "description", ""))
            candidate_descriptions.append(f"{i + 1}. {name}: {persona}")

        candidates_text = "\n".join(candidate_descriptions)
        return (
            "Given the following task, select the single best agent to handle it.\n"
            "Return ONLY the agent name, nothing else.\n\n"
            f"Task: {task}\n\n"
            f"Candidates:\n{candidates_text}\n\n"
            "Best agent:"
        )

    def _find_candidate_by_name(self, name: str, candidates: list[Any]) -> Any | None:
        """Find a candidate matching the given name."""
        name_lower = name.strip().lower()
        for c in candidates:
            c_name = getattr(c, "name", str(c))
            if c_name.lower() == name_lower:
                return c
        return None

    async def allocate(
        self,
        task: str,
        candidates: list[Any],
        create_if_not_found: bool = False,
    ) -> Any | None:
        """Select the best-fit agent using LLM semantic matching.

        Args:
            task: Description of the task to allocate.
            candidates: List of candidate agents.
            create_if_not_found: Reserved for P3.

        Returns:
            The best-fit candidate, or None.

        Raises:
            NotImplementedError: If create_if_not_found=True.
        """
        if create_if_not_found:
            msg = "Dynamic agent creation is reserved for P3 (feature 13.15 Distributed Team Orchestration)"
            raise NotImplementedError(msg)

        if not candidates:
            return None

        prompt = self._build_ranking_prompt(task, candidates)

        try:
            response = await self._collect_llm_response(prompt)
        except Exception:
            logger.warning("LLM invocation failed during task allocation", exc_info=True)
            return None

        match = self._find_candidate_by_name(response, candidates)
        if match is None and candidates:
            return candidates[0]
        return match


class RoundRobinTaskAllocator(TaskAllocator):
    """Round-robin task allocation for simple load balancing.

    Cycles through candidates in order, returning each in turn.
    Suitable for scenarios where all agents are equally capable.
    """

    def __init__(self) -> None:
        self._index: int = 0

    async def allocate(
        self,
        task: str,
        candidates: list[Any],
        create_if_not_found: bool = False,
    ) -> Any | None:
        """Select the next candidate in round-robin order.

        Args:
            task: Description of the task (unused, for interface compatibility).
            candidates: List of candidate agents.
            create_if_not_found: Reserved for P3.

        Returns:
            The next candidate in rotation, or None if empty.

        Raises:
            NotImplementedError: If create_if_not_found=True.
        """
        if create_if_not_found:
            msg = "Dynamic agent creation is reserved for P3 (feature 13.15 Distributed Team Orchestration)"
            raise NotImplementedError(msg)

        if not candidates:
            return None

        selected = candidates[self._index % len(candidates)]
        self._index += 1
        return selected
