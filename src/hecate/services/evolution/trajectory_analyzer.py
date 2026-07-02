"""Trajectory analyzer for extracting success and failure patterns.

Analyzes conversation trajectories using rule-based heuristics to identify
effective tool selections, common failure modes, and improvement opportunities.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_TIMEOUT_THRESHOLD_MS = 25_000.0


@dataclass
class TrajectoryPoint:
    """A single step in a conversation trajectory."""

    step: int
    action: str
    tool_name: str | None = None
    result_summary: str = ""
    success: bool = True
    duration_ms: float = 0.0


@dataclass
class AnalysisResult:
    """Result of analyzing a trajectory."""

    trajectory_id: str
    pattern_type: str  # "success" | "failure"
    key_factors: list[str] = field(default_factory=list)
    improvement_suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    analyzed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _compute_trajectory_id(points: list[TrajectoryPoint]) -> str:
    raw = "|".join(f"{p.step}:{p.action}" for p in points)
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12]  # noqa: S324


def _confidence_for_length(length: int) -> float:
    return min(0.5 + length * 0.05, 0.95)


class TrajectoryAnalyzer:
    """Analyzes conversation trajectories for success/failure patterns."""

    def analyze_success(self, trajectory: list[TrajectoryPoint]) -> AnalysisResult:
        """Extract patterns from a successful trajectory."""
        traj_id = _compute_trajectory_id(trajectory)
        tools_used: list[str] = []
        total_duration = 0.0

        for pt in trajectory:
            if pt.tool_name and pt.success:
                tools_used.append(pt.tool_name)
            total_duration += pt.duration_ms

        factors: list[str] = []
        if tools_used:
            unique_tools = sorted(set(tools_used))
            factors.append(f"Effective tools: {', '.join(unique_tools)}")
        if total_duration > 0:
            avg = total_duration / max(len(trajectory), 1)
            factors.append(f"Average step duration: {avg:.0f}ms")
        if len(trajectory) >= 3:
            factors.append("Multi-step sequential execution succeeded")

        return AnalysisResult(
            trajectory_id=traj_id,
            pattern_type="success",
            key_factors=factors,
            confidence=_confidence_for_length(len(trajectory)),
        )

    def analyze_failure(self, trajectory: list[TrajectoryPoint]) -> AnalysisResult:
        """Extract patterns from a failed trajectory."""
        traj_id = _compute_trajectory_id(trajectory)
        tools_before_failure: list[str] = []
        first_fail_step = -1
        failure_reason = "unknown"

        for i, pt in enumerate(trajectory):
            if not pt.success and first_fail_step < 0:
                first_fail_step = i
                if pt.duration_ms >= _TIMEOUT_THRESHOLD_MS:
                    failure_reason = "timeout"
                failure_reason = pt.result_summary or failure_reason
            if first_fail_step >= 0:
                break
            if pt.tool_name:
                tools_before_failure.append(pt.tool_name)

        suggestions: list[str] = []
        factors: list[str] = [f"Failure at step {first_fail_step}: {failure_reason}"]

        if tools_before_failure:
            factors.append(f"Tools used before failure: {', '.join(tools_before_failure)}")
        if failure_reason == "timeout":
            suggestions.append("Increase execution timeout or simplify tool chain")
        if first_fail_step >= 0 and first_fail_step < len(trajectory) - 1:
            suggestions.append("Investigate intermediate step dependencies")

        if not suggestions:
            suggestions.append("Review step execution order and retry logic")

        return AnalysisResult(
            trajectory_id=traj_id,
            pattern_type="failure",
            key_factors=factors,
            improvement_suggestions=suggestions,
            confidence=_confidence_for_length(len(trajectory)),
        )

    def analyze(self, trajectory: list[TrajectoryPoint]) -> AnalysisResult:
        """Auto-detect success/failure and delegate to the appropriate analyser."""
        if not trajectory:
            return AnalysisResult(
                trajectory_id="empty",
                pattern_type="failure",
                key_factors=["Empty trajectory"],
                improvement_suggestions=["Provide at least one trajectory point"],
                confidence=0.0,
            )

        if trajectory[-1].success:
            return self.analyze_success(trajectory)
        return self.analyze_failure(trajectory)
