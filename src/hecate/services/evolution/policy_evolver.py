"""Policy evolver for adjusting tool and prompt strategies.

Takes trajectory analysis results and adjusts tool priorities and prompt
effectiveness scores using rule-based heuristics (design decision D2).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hecate.services.evolution.trajectory_analyzer import AnalysisResult

logger = logging.getLogger(__name__)

_PRIORITY_BOOST = 0.1
_PRIORITY_PENALTY = 0.1
_MIN_PRIORITY = 0.0
_MAX_PRIORITY = 1.0

_TOOL_RE = re.compile(r"[Tt]ool[s]?\s*:?\s+(\w+)", re.IGNORECASE)


@dataclass
class ToolStrategy:
    """Priority-adjusted tool selection strategy."""

    tool_name: str
    priority: float = 0.5
    last_adjusted: datetime | None = None


@dataclass
class PromptStrategy:
    """Prompt template effectiveness tracking."""

    template_id: str
    effectiveness_score: float = 0.5
    suggested_improvements: list[str] = field(default_factory=list)


def _extract_tools(factors: list[str]) -> list[str]:
    tools: list[str] = []
    for f in factors:
        for match in _TOOL_RE.finditer(f):
            tools.append(match.group(1).lower())
        if "," in f and "tools" in f.lower():
            parts = f.split(":", 1)[-1] if ":" in f else f
            for part in parts.split(","):
                word = part.strip().split()[0] if part.strip() else ""
                if word and word.lower() not in tools:
                    tools.append(word.lower())
    return tools


def _clamp_priority(value: float) -> float:
    return max(_MIN_PRIORITY, min(_MAX_PRIORITY, value))


class PolicyEvolver:
    """Adjusts tool and prompt strategies based on trajectory analysis."""

    def __init__(
        self,
        tool_strategies: dict[str, ToolStrategy] | None = None,
        prompt_strategies: dict[str, PromptStrategy] | None = None,
    ) -> None:
        self._tool_strategies = tool_strategies or {}
        self._prompt_strategies = prompt_strategies or {}

    def adjust_tool_strategy(self, analysis_results: list[AnalysisResult]) -> dict[str, ToolStrategy]:
        """Adjust tool priorities based on success and failure analyses."""
        for result in analysis_results:
            tools = _extract_tools(result.key_factors)
            delta = _PRIORITY_BOOST if result.pattern_type == "success" else -_PRIORITY_PENALTY

            for tool in tools:
                if tool not in self._tool_strategies:
                    self._tool_strategies[tool] = ToolStrategy(tool_name=tool)
                strategy = self._tool_strategies[tool]
                strategy.priority = _clamp_priority(strategy.priority + delta)
                strategy.last_adjusted = datetime.now(UTC)

        logger.info("Adjusted %d tool strategies", len(self._tool_strategies))
        return dict(self._tool_strategies)

    def adjust_prompt_strategy(self, analysis_results: list[AnalysisResult]) -> dict[str, PromptStrategy]:
        """Generate prompt improvement suggestions from failure analyses."""
        for result in analysis_results:
            if result.pattern_type != "failure":
                continue

            template_id = result.trajectory_id
            if template_id not in self._prompt_strategies:
                self._prompt_strategies[template_id] = PromptStrategy(template_id=template_id)
            strategy = self._prompt_strategies[template_id]

            for suggestion in result.improvement_suggestions:
                if "timeout" in suggestion.lower():
                    strategy.suggested_improvements.append(f"add constraint: {suggestion}")
                else:
                    strategy.suggested_improvements.append(f"add example: {suggestion}")

            strategy.effectiveness_score = max(0.0, strategy.effectiveness_score - 0.1)

        for result in analysis_results:
            if result.pattern_type != "success":
                continue
            template_id = result.trajectory_id
            if template_id not in self._prompt_strategies:
                continue
            self._prompt_strategies[template_id].effectiveness_score = min(
                1.0, self._prompt_strategies[template_id].effectiveness_score + 0.05
            )

        return dict(self._prompt_strategies)

    def evolve(
        self, analysis_results: list[AnalysisResult]
    ) -> tuple[dict[str, ToolStrategy], dict[str, PromptStrategy]]:
        """Run both tool and prompt strategy adjustments."""
        tools = self.adjust_tool_strategy(analysis_results)
        prompts = self.adjust_prompt_strategy(analysis_results)
        return tools, prompts

    def get_tool_ranking(self) -> list[tuple[str, float]]:
        """Return tools sorted by priority descending."""
        return sorted(
            ((s.tool_name, s.priority) for s in self._tool_strategies.values()),
            key=lambda x: x[1],
            reverse=True,
        )

    def get_prompt_recommendations(self) -> list[PromptStrategy]:
        """Return prompt strategies sorted by effectiveness descending."""
        return sorted(
            self._prompt_strategies.values(),
            key=lambda s: s.effectiveness_score,
            reverse=True,
        )
