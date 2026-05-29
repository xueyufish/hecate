"""Gray release manager for gradual LLM model rollout.

Supports weighted model routing and time-based progressive rollout
through configurable stages.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GrayReleaseConfig:
    """Configuration for a gray release."""

    release_name: str
    models: dict[str, float] = field(default_factory=dict)
    enabled: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class RolloutSchedule:
    """Timed progression schedule for a gray release."""

    release_name: str
    stages: list[dict] = field(default_factory=list)


class GrayReleaseManager:
    """Manages gradual model rollouts with weighted routing.

    Supports:
    - Weighted random model selection (deterministic with context key)
    - Time-based progressive rollout through configurable stages
    """

    def __init__(self) -> None:
        self._releases: dict[str, GrayReleaseConfig] = {}
        self._schedules: dict[str, RolloutSchedule] = {}

    def create_release(self, config: GrayReleaseConfig) -> None:
        """Register a gray release.

        Normalizes model weights if they do not sum to 1.0.

        Args:
            config: Release configuration.
        """
        config.models = self._normalize_weights(config.models, config.release_name)
        self._releases[config.release_name] = config
        logger.info(f"Created gray release '{config.release_name}' with models: {config.models}")

    def get_release(self, release_name: str) -> GrayReleaseConfig | None:
        """Retrieve a release configuration.

        Args:
            release_name: Name of the release.

        Returns:
            GrayReleaseConfig or None if not found.
        """
        return self._releases.get(release_name)

    def remove_release(self, release_name: str) -> None:
        """Remove a release and its schedule.

        Args:
            release_name: Name of the release to remove.
        """
        self._releases.pop(release_name, None)
        self._schedules.pop(release_name, None)

    def list_releases(self) -> list[str]:
        """List all release names.

        Returns:
            List of release names.
        """
        return list(self._releases.keys())

    def select_model(self, release_name: str, context_key: str | None = None) -> str:
        """Select a model using weighted routing.

        Uses deterministic hash-based assignment when context_key is provided.

        Args:
            release_name: Name of the release.
            context_key: Optional key for deterministic assignment.

        Returns:
            Selected model name.

        Raises:
            KeyError: If release not found.
        """
        config = self._releases.get(release_name)
        if not config:
            raise KeyError(f"Release '{release_name}' not found")

        if not config.enabled or not config.models:
            # Return first model when disabled or empty
            return next(iter(config.models))

        if context_key:
            hash_input = f"{release_name}:{context_key}".encode()
            hash_val = int(hashlib.md5(hash_input).hexdigest(), 16)  # noqa: S324
            ratio = (hash_val % 10000) / 10000.0
        else:
            import random

            ratio = random.random()  # noqa: S311

        return self._weighted_select(config.models, ratio)

    def update_weights(self, release_name: str, new_weights: dict[str, float]) -> None:
        """Update model weights for a release.

        Args:
            release_name: Name of the release.
            new_weights: New model weights (will be normalized).
        """
        config = self._releases.get(release_name)
        if not config:
            logger.warning(f"Release '{release_name}' not found for weight update")
            return

        config.models = self._normalize_weights(new_weights, release_name)
        logger.info(f"Updated weights for '{release_name}': {config.models}")

    def set_rollout_schedule(self, release_name: str, schedule: RolloutSchedule) -> None:
        """Attach a rollout schedule to a release.

        Args:
            release_name: Name of the release.
            schedule: The rollout schedule.
        """
        self._schedules[release_name] = schedule
        logger.info(f"Set rollout schedule for '{release_name}' with {len(schedule.stages)} stages")

    def advance_rollout(self, release_name: str, elapsed_minutes: float) -> None:
        """Advance rollout to the stage matching elapsed time.

        Finds the last stage whose trigger_at_minutes <= elapsed_minutes
        and applies its weights.

        Args:
            release_name: Name of the release.
            elapsed_minutes: Minutes elapsed since release start.
        """
        schedule = self._schedules.get(release_name)
        if not schedule or not schedule.stages:
            return

        # Sort stages by trigger time
        sorted_stages = sorted(schedule.stages, key=lambda s: s.get("trigger_at_minutes", 0))

        target_stage = None
        for stage in sorted_stages:
            if elapsed_minutes >= stage.get("trigger_at_minutes", 0):
                target_stage = stage

        if target_stage:
            new_weights = {
                stage["model_name"]: stage["target_weight"]
                for stage in [target_stage]
                if "model_name" in stage and "target_weight" in stage
            }
            if new_weights:
                self.update_weights(release_name, new_weights)

    def get_current_stage(self, release_name: str, elapsed_minutes: float) -> dict | None:
        """Get the current rollout stage based on elapsed time.

        Args:
            release_name: Name of the release.
            elapsed_minutes: Minutes elapsed since release start.

        Returns:
            Current stage dict or None.
        """
        schedule = self._schedules.get(release_name)
        if not schedule or not schedule.stages:
            return None

        sorted_stages = sorted(schedule.stages, key=lambda s: s.get("trigger_at_minutes", 0))

        current = None
        for stage in sorted_stages:
            if elapsed_minutes >= stage.get("trigger_at_minutes", 0):
                current = stage

        return current

    @staticmethod
    def _weighted_select(weights: dict[str, float], ratio: float) -> str:
        """Select a model from weighted distribution at the given ratio point.

        Args:
            weights: Model name to weight mapping.
            ratio: Float between 0.0 and 1.0.

        Returns:
            Selected model name.
        """
        cumulative = 0.0
        for name, weight in weights.items():
            cumulative += weight
            if ratio < cumulative:
                return name

        # Fallback to last model for floating point edge cases
        return list(weights.keys())[-1]

    @staticmethod
    def _normalize_weights(weights: dict[str, float], release_name: str) -> dict[str, float]:
        """Normalize weights to sum to 1.0.

        Args:
            weights: Raw weights.
            release_name: Release name for logging.

        Returns:
            Normalized weights.
        """
        total = sum(weights.values())
        if total == 0.0:
            logger.warning(f"Release '{release_name}': all weights are zero, using equal distribution")
            count = len(weights)
            return {k: 1.0 / count for k in weights} if count > 0 else {}

        if abs(total - 1.0) > 0.01:
            logger.warning(
                f"Release '{release_name}': weights sum to {total:.4f}, normalizing to 1.0"
            )

        return {k: v / total for k, v in weights.items()}
