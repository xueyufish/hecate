"""Fusion ranking primitives for fact-memory retrieval.

Pure scoring functions behind the memory-importance-fusion change:

- relevance normalization (min-max within one query's candidate set),
- read-time time-decay multiplier anchored on ``last_confirmed_at``,
- importance multiplier (the 0.5 storage default maps to neutral 1.0x),
- bounded multiplicative bias with a product floor — a ranking nudge,
  never a filter,
- ``score_breakdown`` assembly for observability.

Bias participation is governed by ``MEMORY_FUSION_BIAS_ENABLED`` (default
false): with the flag off every multiplier is 1.0, no floor is applied,
and fused order equals normalized-relevance order. Settings are read
lazily with hard-coded fallbacks so tests and third-party hosts without
those fields still get well-defined behavior.

Design constraints (from the change's design.md): the four signals are
NEVER combined as a linear weighted sum — relevance signals are
incommensurable with metadata signals, and access frequency never enters
the query-time formula at all (it is endogenous to the ranking).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

_BIAS_FLAG = "MEMORY_FUSION_BIAS_ENABLED"
_HALFLIFE_EPISODIC = "MEMORY_FUSION_HALFLIFE_EPISODIC_DAYS"
_HALFLIFE_SEMANTIC = "MEMORY_FUSION_HALFLIFE_SEMANTIC_DAYS"
_HALFLIFE_KNOWLEDGE = "MEMORY_FUSION_HALFLIFE_KNOWLEDGE_DAYS"
_MULT_MIN = "MEMORY_FUSION_IMPORTANCE_MULT_MIN"
_MULT_MAX = "MEMORY_FUSION_IMPORTANCE_MULT_MAX"
_PRODUCT_FLOOR = "MEMORY_FUSION_PRODUCT_FLOOR"

_DEFAULT_HALFLIFE_EPISODIC = 30.0
_DEFAULT_HALFLIFE_SEMANTIC = 180.0
_DEFAULT_HALFLIFE_KNOWLEDGE = 0.0
_DEFAULT_MULT_MIN = 0.5
_DEFAULT_MULT_MAX = 1.5
_DEFAULT_PRODUCT_FLOOR = 0.3

_NEUTRAL_IMPORTANCE = 0.5


def _float_setting(name: str, default: float) -> float:
    try:
        from hecate.core.config import settings

        return float(getattr(settings, name, default))
    except Exception:
        return default


def bias_enabled() -> bool:
    """Whether the metadata bias participates in ranking (default off)."""
    try:
        from hecate.core.config import settings

        return bool(getattr(settings, _BIAS_FLAG, False))
    except Exception:
        return False


def normalize_scores(scores: list[float]) -> list[float]:
    """Min-max normalize scores within one query's candidate set.

    A single candidate, an empty list, or a uniform candidate set maps to
    all-1.0 (equally relevant — never zeroed). Normalized scores are
    window-local by construction: never compare them across queries or
    apply absolute thresholds to them.
    """
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi <= lo:
        return [1.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def half_life_days_for(memory_type: str, *, knowledge_layer: bool = False) -> float:
    """Decay half-life in days for a memory; 0 means evergreen."""
    if knowledge_layer:
        return _float_setting(_HALFLIFE_KNOWLEDGE, _DEFAULT_HALFLIFE_KNOWLEDGE)
    if memory_type == "episodic":
        return _float_setting(_HALFLIFE_EPISODIC, _DEFAULT_HALFLIFE_EPISODIC)
    return _float_setting(_HALFLIFE_SEMANTIC, _DEFAULT_HALFLIFE_SEMANTIC)


def decay_multiplier(
    last_confirmed_at: datetime | None,
    created_at: datetime,
    half_life_days: float,
    *,
    now: datetime | None = None,
) -> float:
    """Read-time exponential decay anchored on the confirmation time.

    ``anchor = last_confirmed_at or created_at`` — the anchor is refreshed
    only by consolidation writes, never by retrieval. ``half_life_days <= 0``
    means evergreen (1.0). A missing/naive anchor or a future timestamp
    yields full weight (age clamped at zero).
    """
    if half_life_days <= 0:
        return 1.0
    anchor = last_confirmed_at or created_at
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age_days = max((now - anchor).total_seconds(), 0.0) / 86400.0
    return math.exp(-math.log(2.0) * age_days / half_life_days)


def importance_multiplier(importance: float, *, mult_min: float | None = None, mult_max: float | None = None) -> float:
    """Linear importance mapping: the 0.5 default is the neutral 1.0x."""
    lo = _float_setting(_MULT_MIN, _DEFAULT_MULT_MIN) if mult_min is None else mult_min
    hi = _float_setting(_MULT_MAX, _DEFAULT_MULT_MAX) if mult_max is None else mult_max
    if _NEUTRAL_IMPORTANCE <= 0:
        return 1.0
    scaled = importance / _NEUTRAL_IMPORTANCE
    return max(lo, min(hi, scaled))


def fuse(
    relevance: float,
    decay: float,
    importance_mult: float,
    *,
    floor: float | None = None,
) -> tuple[float, dict[str, float]]:
    """Bounded multiplicative fusion with a product floor.

    The floor applies only while the bias is enabled (it is a ranking
    nudge, never a filter, but with the feature off the output must equal
    pure relevance exactly). Returns the fused score plus its breakdown;
    with the bias off the breakdown reports neutral multipliers.
    """
    if bias_enabled():
        product = max(relevance, 0.0) * decay * importance_mult
        effective_floor = _float_setting(_PRODUCT_FLOOR, _DEFAULT_PRODUCT_FLOOR) if floor is None else floor
        final = max(product, effective_floor)
        breakdown = {
            "relevance": round(relevance, 4),
            "decay_mult": round(decay, 4),
            "importance_mult": round(importance_mult, 4),
        }
    else:
        final = max(relevance, 0.0)
        breakdown = {"relevance": round(final, 4), "decay_mult": 1.0, "importance_mult": 1.0}
    return final, breakdown


def breakdown_for_hit(
    *,
    relevance: float | None,
    last_confirmed_at: datetime | None,
    created_at: datetime,
    importance: float,
    knowledge_layer: bool = False,
    memory_type: str = "semantic",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the per-signal breakdown for one candidate.

    ``relevance=None`` marks a candidate with no real vector (no semantic
    signal): it is reported with zero relevance and neutral multipliers so
    consumers can distinguish "scored low" from "not semantically scored".
    """
    if relevance is None:
        return {"relevance": 0.0, "decay_mult": 1.0, "importance_mult": 1.0, "semantic": False}
    decay = decay_multiplier(
        last_confirmed_at,
        created_at,
        half_life_days_for(memory_type, knowledge_layer=knowledge_layer),
        now=now,
    )
    mult = importance_multiplier(importance)
    _, parts = fuse(relevance, decay, mult)
    parts["semantic"] = True
    return parts


__all__ = [
    "bias_enabled",
    "breakdown_for_hit",
    "cosine_similarity",
    "decay_multiplier",
    "fuse",
    "half_life_days_for",
    "importance_multiplier",
    "normalize_scores",
]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length dense vectors (0 on mismatch)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)
