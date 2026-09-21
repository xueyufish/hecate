"""Offline memory value score for the consolidation pass.

Computes a per-memory value used for observability and (in v2) eviction
decisions. Strictly: this score is observational — the score, its
components, and the offline nature are documented as never driving any
delete / soft-delete / disable action in the current change.

The formula blends:

- confirmation freshness: half-life decay from the memory's
  ``last_confirmed_at`` (set at insert, refreshed by consolidation
  writes);
- access heat: log-compressed distinct-session hit count, with its own
  half-life decay since the last access.

Weights and half-lives are settings-backed; defaults are conservative.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime


def _float_setting(name: str, default: float) -> float:
    try:
        from hecate.core.config import settings

        return float(getattr(settings, name, default))
    except Exception:
        return default


def _weights() -> tuple[float, float]:
    w_conf = _float_setting("MEMORY_VALUE_W_CONFIRMATION", 0.65)
    w_acc = _float_setting("MEMORY_VALUE_W_ACCESS", 0.35)
    return w_conf, w_acc


def _half_life_confirmation_days() -> float:
    return max(_float_setting("MEMORY_VALUE_HALFLIFE_CONFIRMATION_DAYS", 90.0), 0.001)


def _half_life_access_days() -> float:
    return max(_float_setting("MEMORY_VALUE_HALFLIFE_ACCESS_DAYS", 30.0), 0.001)


def _log1p_cap(distinct_session_count: int) -> float:
    """Log-compress distinct-session count, capped to a smooth 0..1 band."""
    # log1p maps 0->0 and ~9 -> ~1; log1p(10)/log1p(2)... let cap sit at
    # log1p(64)/log(64)~0.31, here we map onto log1p(n)/log1p(64).
    n = max(int(distinct_session_count), 0)
    if n == 0:
        return 0.0
    return math.log1p(n) / math.log1p(64.0)


def _exp_decay(age_seconds: float, half_life_days: float) -> float:
    return math.exp(-math.log(2.0) * age_seconds / (half_life_days * 86400.0))


def _ensure_utc(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def compute(
    *,
    last_confirmed_at: datetime | None,
    created_at: datetime,
    last_accessed_at: datetime | None,
    distinct_session_count: int,
    now: datetime | None = None,
) -> tuple[float, dict[str, float]]:
    """Return (total_value, components_dict) for one memory row.

    Components are clamped to [0, 1]; the total is a convex combination
    of the two components. The function is pure: it never reads or
    writes anything.
    """
    w_conf, w_acc = _weights()
    now = _ensure_utc(now or datetime.now(UTC))

    confirmation_anchor = last_confirmed_at or created_at
    confirmation_anchor = _ensure_utc(confirmation_anchor)
    confirmation_age = max((now - confirmation_anchor).total_seconds(), 0.0)
    confirmation_freshness = _exp_decay(confirmation_age, _half_life_confirmation_days())

    if last_accessed_at is None:
        access_heat = 0.0
    else:
        access_anchor = _ensure_utc(last_accessed_at)
        access_age = max((now - access_anchor).total_seconds(), 0.0)
        access_heat = _log1p_cap(distinct_session_count) * _exp_decay(access_age, _half_life_access_days())

    components = {
        "confirmation_freshness": round(max(min(confirmation_freshness, 1.0), 0.0), 4),
        "access_heat": round(max(min(access_heat, 1.0), 0.0), 4),
    }
    total = round(max(min(w_conf * confirmation_freshness + w_acc * access_heat, 1.0), 0.0), 4)
    return total, components


__all__ = ["compute"]
