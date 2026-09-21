"""Unit tests for the fusion ranking primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hecate_memory.memory import ranking


def test_normalize_scores_uniform_returns_all_ones() -> None:
    assert ranking.normalize_scores([0.5, 0.5, 0.5]) == [1.0, 1.0, 1.0]


def test_normalize_scores_empty_returns_empty() -> None:
    assert ranking.normalize_scores([]) == []


def test_normalize_scores_zero_division_safe() -> None:
    # All-zero is treated as uniform (otherwise min-max would 0/0).
    assert ranking.normalize_scores([0.0, 0.0]) == [1.0, 1.0]


def test_decay_mult_evergreen_is_one() -> None:
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    assert ranking.decay_multiplier(anchor, anchor, half_life_days=0.0) == 1.0
    assert ranking.decay_multiplier(anchor, anchor, half_life_days=-1.0) == 1.0


def test_decay_mult_half_life_halves_score() -> None:
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    now = anchor + timedelta(days=30)
    value = ranking.decay_multiplier(anchor, anchor, half_life_days=30.0, now=now)
    assert abs(value - 0.5) < 1e-9


def test_decay_mult_falls_back_to_created_at_when_anchor_missing() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    now = created + timedelta(days=30)
    assert abs(ranking.decay_multiplier(None, created, 30.0, now=now) - 0.5) < 1e-9


def test_decay_mult_naive_anchor_is_treated_as_utc() -> None:
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert ranking.decay_multiplier(naive, aware, 30.0, now=aware + timedelta(days=15)) == ranking.decay_multiplier(
        aware, aware, 30.0, now=aware + timedelta(days=15)
    )


def test_importance_multiplier_neutral_default() -> None:
    assert ranking.importance_multiplier(0.5) == 1.0


def test_importance_multiplier_clamped_to_bounds() -> None:
    # 0.0 importance is below 0.5/0.5*0.5=0.5; clamp to mult_min
    assert ranking.importance_multiplier(0.0, mult_min=0.5, mult_max=1.5) == 0.5
    # 1.0 importance is 1.0/0.5=2.0; clamp to mult_max
    assert ranking.importance_multiplier(1.0, mult_min=0.5, mult_max=1.5) == 1.5


def test_fuse_bias_off_returns_relevance_unchanged() -> None:
    # Force-bias-off path by reading setting; the function reads it lazily.
    final, parts = ranking.fuse(0.42, 0.5, 1.5, floor=0.0)
    assert final == 0.42
    assert parts["decay_mult"] == 1.0
    assert parts["importance_mult"] == 1.0


def test_breakdown_for_hit_no_semantic_signal() -> None:
    parts = ranking.breakdown_for_hit(
        relevance=None,
        last_confirmed_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        importance=0.5,
    )
    assert parts["semantic"] is False
    assert parts["relevance"] == 0.0
    assert parts["decay_mult"] == 1.0
    assert parts["importance_mult"] == 1.0


def test_cosine_similarity_basic() -> None:
    assert ranking.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert ranking.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert ranking.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_similarity_mismatched_lengths() -> None:
    assert ranking.cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_similarity_zero_vector() -> None:
    assert ranking.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_half_life_for_memory_type() -> None:
    assert ranking.half_life_days_for("episodic") == 30.0
    assert ranking.half_life_days_for("semantic") == 180.0
    assert ranking.half_life_days_for("procedural") == 180.0
    assert ranking.half_life_days_for("anything-else") == 180.0
    assert ranking.half_life_days_for("semantic", knowledge_layer=True) == 0.0
