"""Unit tests for the offline memory value score."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hecate_memory.memory.value_score import compute


def test_value_score_recent_confirmation_full_freshness() -> None:
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    total, parts = compute(
        last_confirmed_at=anchor,
        created_at=anchor,
        last_accessed_at=None,
        distinct_session_count=0,
        now=anchor + timedelta(minutes=1),
    )
    assert parts["confirmation_freshness"] == 1.0
    assert parts["access_heat"] == 0.0
    assert 0.0 <= total <= 1.0


def test_value_score_aged_confirmation_decays() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    now = created + timedelta(days=90)  # one half-life by default
    total, parts = compute(
        last_confirmed_at=created,
        created_at=created,
        last_accessed_at=None,
        distinct_session_count=0,
        now=now,
    )
    assert abs(parts["confirmation_freshness"] - 0.5) < 1e-9
    # weight is 0.65 confirmation + 0.35 access(0)
    assert abs(total - 0.65 * 0.5) < 1e-9


def test_value_score_access_heat_capped_and_decays() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    last_access = created + timedelta(days=30)
    now = last_access + timedelta(days=30)  # one half-life since access
    _, parts = compute(
        last_confirmed_at=created,
        created_at=created,
        last_accessed_at=last_access,
        distinct_session_count=64,
        now=now,
    )
    # 64 sessions → cap 1.0; half-life since access → 0.5
    assert abs(parts["access_heat"] - 0.5) < 1e-9


def test_value_score_total_is_convex_combination() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    total, _ = compute(
        last_confirmed_at=created,
        created_at=created,
        last_accessed_at=None,
        distinct_session_count=0,
    )
    assert 0.0 <= total <= 1.0


def test_value_score_no_access_recorded_is_zero() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    _, parts = compute(
        last_confirmed_at=created,
        created_at=created,
        last_accessed_at=None,
        distinct_session_count=99,
    )
    assert parts["access_heat"] == 0.0
