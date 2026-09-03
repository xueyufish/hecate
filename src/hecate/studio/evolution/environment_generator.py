"""Synthetic environment generator for agent training.

Creates rule-based training environments with configurable difficulty,
tool availability, and constraints. Validates that generated environments
achieve a target success rate of 20-60%.
"""

from __future__ import annotations

import logging
import random  # noqa: S311
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_CAPABILITY_MAP: dict[str, dict[str, Any]] = {
    "tool_selection": {
        "tools": ["search", "calculator", "translator"],
        "constraints": ["max_3_calls", "time_limit_30s"],
    },
    "reasoning": {
        "tools": ["calculator"],
        "constraints": ["show_work", "step_by_step"],
    },
    "coding": {
        "tools": ["code_executor", "test_runner"],
        "constraints": ["type_hints_required", "no_assert"],
    },
    "analysis": {
        "tools": ["search", "calculator", "data_query"],
        "constraints": ["cite_sources", "quantify_claims"],
    },
}

_DEFAULT_ENV = {
    "tools": ["search"],
    "constraints": ["time_limit_60s"],
}

_MIN_DIFFICULTY = 0.2
_MAX_DIFFICULTY = 0.6


@dataclass
class EnvironmentConfig:
    """Configuration for a synthetic training environment."""

    name: str
    difficulty: float = 0.5
    tools_available: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    target_success_rate: float = 0.4


@dataclass
class EnvironmentValidation:
    """Validation result for a synthetic environment."""

    environment_name: str
    is_valid: bool = False
    estimated_success_rate: float = 0.0
    difficulty_score: float = 0.0
    adjustments: list[str] = field(default_factory=list)


def _scale_tools(base_tools: list[str], difficulty: float) -> list[str]:
    available = list(base_tools)
    max_tools = max(1, round(len(available) * (1.1 - difficulty)))
    if len(available) > max_tools:
        random.shuffle(available)
        available = available[:max_tools]
    return available


def _scale_constraints(base_constraints: list[str], difficulty: float) -> list[str]:
    result = list(base_constraints)
    extra = int(difficulty * 2)
    for i in range(extra):
        result.append(f"bonus_constraint_{i + 1}")
    return result


class SyntheticEnvironmentGenerator:
    """Generates and validates synthetic training environments."""

    def generate_environment(self, capability: str, difficulty: float = 0.5) -> EnvironmentConfig:
        """Generate a training environment for a specific capability."""
        env_def = _CAPABILITY_MAP.get(capability, _DEFAULT_ENV)
        tools = _scale_tools(env_def["tools"], difficulty)
        constraints = _scale_constraints(env_def["constraints"], difficulty)

        env = EnvironmentConfig(
            name=f"{capability}_difficulty_{difficulty:.1f}",
            difficulty=difficulty,
            tools_available=tools,
            constraints=constraints,
            target_success_rate=round((1.0 - difficulty) * 0.8 + 0.1, 2),
        )
        logger.info(
            "Generated environment '%s': %d tools, %d constraints",
            env.name,
            len(env.tools_available),
            len(env.constraints),
        )
        return env

    def validate_difficulty(self, environment: EnvironmentConfig) -> EnvironmentValidation:
        """Validate environment difficulty is in the 20-60% success rate band."""
        estimated = (1.0 - environment.difficulty) * 0.8 + 0.1
        adjustments: list[str] = []

        if environment.difficulty > _MAX_DIFFICULTY:
            adjustments.append(f"Difficulty {environment.difficulty:.2f} too high — relax constraints or add tools")
        elif environment.difficulty < _MIN_DIFFICULTY:
            adjustments.append(
                f"Difficulty {environment.difficulty:.2f} too low — add constraints or reduce available tools"
            )

        is_valid = _MIN_DIFFICULTY <= environment.difficulty <= _MAX_DIFFICULTY
        return EnvironmentValidation(
            environment_name=environment.name,
            is_valid=is_valid,
            estimated_success_rate=round(estimated, 2),
            difficulty_score=environment.difficulty,
            adjustments=adjustments,
        )

    def generate_batch(
        self,
        capabilities: list[str],
        difficulty_range: tuple[float, float] = (0.3, 0.5),
    ) -> list[EnvironmentConfig]:
        """Generate environments for multiple capabilities with varying difficulty."""
        lo, hi = difficulty_range
        environments: list[EnvironmentConfig] = []
        step = (hi - lo) / max(len(capabilities), 1)
        for i, cap in enumerate(capabilities):
            diff = round(lo + step * i, 2)
            environments.append(self.generate_environment(cap, diff))
        return environments
