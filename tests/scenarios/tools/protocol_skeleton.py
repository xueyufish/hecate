"""Structural skeletons for golden protocol comparison.

A skeleton captures the recursive SHAPE of a JSON payload — key sets and
value types — while discarding all text-bearing values. Comparing skeletons
therefore detects protocol drift (missing/renamed/retyped fields) without
ever comparing model text verbatim, which is the scenario pack's contract
for old-path migration baselines (S10).
"""

from __future__ import annotations

from typing import Any


def skeleton(value: Any) -> Any:
    """Reduce a JSON-like value to a recursive type/key structure.

    - dicts  -> {key: skeleton(v)} with sorted keys for stable comparison
    - lists  -> {"list_of": skeleton(first)} (empty -> {"list_of": None})
    - None   -> "null"
    - scalar -> its type name (str / int / float / bool)
    """
    if isinstance(value, dict):
        return {key: skeleton(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return {"list_of": skeleton(value[0]) if value else None}
    if value is None:
        return "null"
    return type(value).__name__


def matches(recorded: Any, live: Any) -> bool:
    """True when the live payload's skeleton equals the recorded structure."""
    return skeleton(live) == recorded
