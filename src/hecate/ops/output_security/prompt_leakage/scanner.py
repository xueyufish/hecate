"""Prompt leakage scanner facade (9.2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from hecate.ops.dlp.result import DLPAction
from hecate.ops.output_security.prompt_leakage.fingerprint import (
    DEFAULT_NGRAM_SIZE,
    DEFAULT_WINDOW_SIZE,
    find_matched_indices,
    fingerprint,
    overlap_ratio,
)
from hecate.ops.output_security.prompt_leakage.severity import classify

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptLeakageConfig:
    """Resolved configuration for the prompt leakage scanner."""

    enabled: bool
    threshold: float
    action: DLPAction
    ngram_size: int
    window_size: int


@dataclass(frozen=True)
class PromptLeakageFinding:
    """Single prompt-leakage detection emitted by ``scan``."""

    severity: str
    category: str
    overlap_ratio: float
    action: DLPAction
    recognizer: str
    entity_type: str
    matched_substring: str
    match_offset_start: int
    match_offset_end: int


def resolve_config(guardrail_cfg: dict | None) -> PromptLeakageConfig:
    """Translate the raw ``prompt_leakage`` config dict to ``PromptLeakageConfig``."""
    cfg = (guardrail_cfg or {}).get("prompt_leakage") or {}
    enabled = bool(cfg.get("enabled", True))
    threshold = float(cfg.get("threshold", 0.20))
    threshold = max(0.05, min(0.80, threshold))
    action_value = cfg.get("action", "block")
    action = DLPAction.SANITIZE if action_value == "sanitize" else DLPAction.BLOCK
    ngram_size = int(cfg.get("ngram_size", DEFAULT_NGRAM_SIZE))
    if ngram_size < 2:
        ngram_size = DEFAULT_NGRAM_SIZE
    window_size = int(cfg.get("window_size", DEFAULT_WINDOW_SIZE))
    if window_size < 1:
        window_size = DEFAULT_WINDOW_SIZE
    return PromptLeakageConfig(
        enabled=enabled,
        threshold=threshold,
        action=action,
        ngram_size=ngram_size,
        window_size=window_size,
    )


def scan(
    response_content: str,
    *,
    baseline_fingerprint: set[int],
    config: PromptLeakageConfig,
) -> PromptLeakageFinding | None:
    """Compare ``response_content`` against ``baseline_fingerprint``.

    Returns ``None`` when overlap is below threshold. Returns a finding
    otherwise with the most-restrictive merged action.
    """
    if not config.enabled:
        return None
    if not isinstance(response_content, str) or not response_content:
        return None
    if not baseline_fingerprint:
        return None

    candidate_fp = fingerprint(
        response_content,
        n=config.ngram_size,
        window=config.window_size,
    )
    ratio = overlap_ratio(baseline_fingerprint, candidate_fp)
    if ratio <= config.threshold:
        return None

    matched = find_matched_indices(
        response_content,
        baseline_fingerprint=baseline_fingerprint,
        n=config.ngram_size,
    )
    if not matched:
        return PromptLeakageFinding(
            severity="high",
            category="persona",
            overlap_ratio=ratio,
            action=config.action,
            recognizer="prompt_leakage",
            entity_type="PROMPT_LEAKAGE",
            matched_substring="",
            match_offset_start=0,
            match_offset_end=0,
        )
    first_start, first_end, first_gram = matched[0]
    context_window = response_content[max(0, first_start - 50) : min(len(response_content), first_end + 50)]
    category, severity = classify(first_gram, context_window=context_window)
    return PromptLeakageFinding(
        severity=severity,
        category=category,
        overlap_ratio=ratio,
        action=config.action,
        recognizer="prompt_leakage",
        entity_type="PROMPT_LEAKAGE",
        matched_substring=first_gram,
        match_offset_start=first_start,
        match_offset_end=first_end,
    )


def scan_with_guardrail_cfg(
    response_content: str,
    *,
    baseline_fingerprint: set[int],
    guardrail_cfg: dict | None,
) -> PromptLeakageFinding | None:
    """Convenience wrapper: resolve config + scan in one call."""
    return scan(
        response_content,
        baseline_fingerprint=baseline_fingerprint,
        config=resolve_config(guardrail_cfg),
    )
