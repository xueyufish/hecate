"""Intent package publish gate (6.49).

A gate decides whether an intent package version may be published. The
decision is purely deterministic: coverage is computed from the frozen
content itself, and the accuracy signal reads a pre-resolved recognition
result (produced by the evaluation linkage, task group 7) that itself only
contains deterministic scores. LLM-judge and human scores never reach this
module, mirroring the workflow publish gate (:mod:`hecate.ops.evaluation.publish_gate`).

The gate is a pure function of (config, version content, linked result):
the caller (publish service) resolves the linkage and renders the 409
envelope from the returned verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PackageGateConfig:
    """Resolved publish-gate configuration.

    ``raw is None`` maps to mode ``off`` — the publish proceeds without
    gate evaluation.
    """

    mode: str  # "off" | "warn" | "require"
    min_pass_rate: float | None
    min_samples_per_category: int | None


@dataclass
class PackageGateSignal:
    """One signal's verdict."""

    name: str
    enabled: bool
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PackageGateResult:
    """Outcome of evaluating the publish gate for one version."""

    mode: str
    signals: list[PackageGateSignal]
    coverage: dict[str, int]
    linked_run_id: str | None

    @property
    def blocking(self) -> bool:
        return self.mode == "require" and any(not s.passed for s in self.signals)

    def to_report_payload(self, bypassed: bool) -> dict:
        """Render the ``gate`` block recorded on the version."""
        return {
            "mode": self.mode,
            "bypassed_by_force": bypassed,
            "coverage": self.coverage,
            "linked_run_id": self.linked_run_id,
            "signals": [
                {
                    "name": signal.name,
                    "enabled": signal.enabled,
                    "passed": signal.passed,
                    "detail": signal.detail,
                }
                for signal in self.signals
            ],
        }


def resolve_gate_config(raw: dict | None) -> PackageGateConfig:
    """Normalize an incoming gate config dict; unknown modes raise."""
    if raw is None:
        return PackageGateConfig(mode="off", min_pass_rate=None, min_samples_per_category=None)
    mode = str(raw.get("mode") or "").strip()
    if mode not in ("warn", "require"):
        msg = f"gate.mode must be 'warn' or 'require', got {mode!r}"
        raise ValueError(msg)
    min_pass_rate = raw.get("min_pass_rate")
    min_samples = raw.get("min_samples_per_category")
    return PackageGateConfig(
        mode=mode,
        min_pass_rate=float(min_pass_rate) if min_pass_rate is not None else None,
        min_samples_per_category=int(min_samples) if min_samples is not None else None,
    )


def validate_gate_config(raw: dict) -> None:
    """Service-layer validation: require mode needs at least one signal."""
    config = resolve_gate_config(raw)
    if config.mode == "require" and not any(
        (config.min_pass_rate is not None, config.min_samples_per_category is not None)
    ):
        msg = "gate mode='require' needs at least one enabled signal"
        raise ValueError(msg)


def category_coverage(content: dict) -> dict[str, int]:
    """Sample count per frozen category, in freeze order."""
    return {str(category.get("name")): len(category.get("samples", [])) for category in content.get("categories", [])}


def evaluate_gate(
    config: PackageGateConfig,
    content: dict,
    linked_result: dict | None = None,
) -> PackageGateResult:
    """Compute the gate verdict for a publish attempt.

    Args:
        config: Resolved gate configuration.
        content: The version's frozen content payload.
        linked_result: Recognition-accuracy result for this version, as
            resolved by the evaluation linkage: ``{"run_id": str,
            "pass_rate": float, "per_category": {name: float}}`` or None
            when no completed run is linked. Only deterministic scores
            reach this structure upstream.

    Returns:
        A populated :class:`PackageGateResult` — never raises for signal
        outcomes; configuration errors raise at :func:`resolve_gate_config`.
    """
    if config.mode == "off":
        return PackageGateResult(mode="off", signals=[], coverage={}, linked_run_id=None)

    coverage = category_coverage(content)
    signals: list[PackageGateSignal] = []

    if config.min_samples_per_category is not None:
        shortfall = {name: count for name, count in coverage.items() if count < config.min_samples_per_category}
        signals.append(
            PackageGateSignal(
                name="min_samples_per_category",
                enabled=True,
                passed=not shortfall,
                detail={
                    "min_samples_per_category": config.min_samples_per_category,
                    "shortfall": shortfall,
                },
            )
        )

    if config.min_pass_rate is not None:
        if not linked_result or linked_result.get("pass_rate") is None:
            signals.append(
                PackageGateSignal(
                    name="min_pass_rate",
                    enabled=True,
                    passed=False,
                    detail={
                        "reason": "no_linked_run",
                        "min_pass_rate": config.min_pass_rate,
                    },
                )
            )
        else:
            pass_rate = float(linked_result["pass_rate"])
            signals.append(
                PackageGateSignal(
                    name="min_pass_rate",
                    enabled=True,
                    passed=pass_rate >= config.min_pass_rate,
                    detail={
                        "min_pass_rate": config.min_pass_rate,
                        "recognition_pass_rate": pass_rate,
                        "per_category": dict(linked_result.get("per_category") or {}),
                    },
                )
            )

    linked_run_id = str(linked_result["run_id"]) if linked_result and linked_result.get("run_id") else None
    return PackageGateResult(
        mode=config.mode,
        signals=signals,
        coverage=coverage,
        linked_run_id=linked_run_id,
    )


__all__ = [
    "PackageGateConfig",
    "PackageGateResult",
    "PackageGateSignal",
    "category_coverage",
    "evaluate_gate",
    "resolve_gate_config",
    "validate_gate_config",
]
