"""Tool filtering based on detected task phase.

Filters the available tool list to present only tools relevant to the
current task phase, reducing context noise and improving tool selection.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.context.types import PhaseToolMapping, TaskPhase

logger = logging.getLogger(__name__)


class ToolFilter:
    """Filters tools based on the current task phase.

    Different phases of work benefit from different tool sets:
    - Explore: search, read, list tools
    - Converge: search, read, edit tools
    - Execute: all tools (no filtering)
    - Verify: read, test, check tools
    """

    def __init__(self, mapping: PhaseToolMapping | None = None) -> None:
        """Initialize the tool filter.

        Args:
            mapping: Optional custom phase-to-tool mapping.
                If None, uses default mapping.
        """
        self.mapping = mapping or PhaseToolMapping()

    def filter_tools(
        self,
        tools: list[dict[str, Any]],
        phase: TaskPhase,
    ) -> list[dict[str, Any]]:
        """Filter tools based on the current task phase.

        Args:
            tools: Full list of tool definitions (OpenAI format).
            phase: Current task phase.

        Returns:
            Filtered list of tools appropriate for the phase.
        """
        # Get allowed tool categories for this phase
        allowed_categories = self._get_allowed_categories(phase)

        # If no filtering needed (execute phase or empty mapping), return all
        if not allowed_categories:
            return tools

        # Filter tools by category
        filtered = []
        for tool in tools:
            tool_name = self._get_tool_name(tool)
            if self._matches_categories(tool_name, allowed_categories):
                filtered.append(tool)

        # If filtering removed all tools, return original set as safety fallback
        if not filtered and tools:
            logger.warning(f"Tool filtering removed all tools for phase {phase.value}, returning full set")
            return tools

        logger.debug(f"Filtered {len(tools)} tools to {len(filtered)} for phase {phase.value}")
        return filtered

    def _get_allowed_categories(self, phase: TaskPhase) -> list[str]:
        """Get allowed tool categories for a phase.

        Args:
            phase: Current task phase.

        Returns:
            List of allowed tool category names. Empty means all allowed.
        """
        match phase:
            case TaskPhase.EXPLORE:
                return self.mapping.explore
            case TaskPhase.CONVERGE:
                return self.mapping.converge
            case TaskPhase.EXECUTE:
                return self.mapping.execute
            case TaskPhase.VERIFY:
                return self.mapping.verify
            case _:
                return []

    def _get_tool_name(self, tool: dict[str, Any]) -> str:
        """Extract tool name from tool definition.

        Args:
            tool: Tool definition dict.

        Returns:
            Tool name string.
        """
        # OpenAI format: {"type": "function", "function": {"name": "..."}}
        func: dict[str, Any] | Any = tool.get("function", {})
        if isinstance(func, dict):
            return str(func.get("name", ""))
        return ""

    def _matches_categories(
        self,
        tool_name: str,
        allowed_categories: list[str],
    ) -> bool:
        """Check if a tool matches any allowed category.

        Args:
            tool_name: Name of the tool.
            allowed_categories: List of allowed category keywords.

        Returns:
            True if tool matches any category.
        """
        tool_name_lower = tool_name.lower()

        for category in allowed_categories:
            # Direct match
            if category in tool_name_lower:
                return True
            # Common tool name patterns
            if category == "read" and any(kw in tool_name_lower for kw in ["read", "get", "fetch", "load", "view"]):
                return True
            if category == "write" and any(
                kw in tool_name_lower for kw in ["write", "create", "save", "update", "edit", "modify"]
            ):
                return True
            if category == "search" and any(
                kw in tool_name_lower for kw in ["search", "find", "grep", "glob", "query", "list"]
            ):
                return True
            if category == "test" and any(
                kw in tool_name_lower for kw in ["test", "check", "verify", "validate", "lint", "format"]
            ):
                return True
            if category == "execute" and any(
                kw in tool_name_lower for kw in ["run", "exec", "bash", "shell", "command"]
            ):
                return True

        return False
