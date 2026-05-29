"""Integration wiring between self-evolution services and harness/evidence tracking.

Provides convenience functions to trigger trajectory analysis on evidence
completion and feed analysis results into the harness optimization loop.
"""

from __future__ import annotations

import logging

from hecate.services.evolution.policy_evolver import PolicyEvolver
from hecate.services.evolution.trajectory_analyzer import (
    AnalysisResult,
    TrajectoryAnalyzer,
    TrajectoryPoint,
)

logger = logging.getLogger(__name__)


class EvolutionIntegrator:
    """Bridges evidence tracking with trajectory analysis and policy evolution."""

    def __init__(
        self,
        analyzer: TrajectoryAnalyzer | None = None,
        evolver: PolicyEvolver | None = None,
    ) -> None:
        self._analyzer = analyzer or TrajectoryAnalyzer()
        self._evolver = evolver or PolicyEvolver()

    def analyze_trajectory(self, trajectory: list[TrajectoryPoint]) -> AnalysisResult:
        """Analyze a trajectory and feed results into the policy evolver."""
        result = self._analyzer.analyze(trajectory)
        self._evolver.evolve([result])
        logger.info(
            "Analyzed trajectory %s: %s (%d suggestions)",
            result.trajectory_id,
            result.pattern_type,
            len(result.improvement_suggestions),
        )
        return result

    def get_evolved_strategies(self, results: list[AnalysisResult]) -> tuple[dict, list]:
        """Return evolved tool and prompt strategies from accumulated analyses."""
        tools, prompts = self._evolver.evolve(results)
        return tools, list(prompts.values())
