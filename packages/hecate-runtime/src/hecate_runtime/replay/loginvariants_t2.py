"""T2 invariants — Approval pair turn-closure (T2.2).

Enforces that every ``APPROVAL_ASKED`` is paired with an
``APPROVAL_DECIDED`` whose ``tool_call_id`` matches and whose event version
is greater than the asked event's — and that the pair lives within a
``TURN_START`` / ``TURN_END`` window.

Cross-turn pairs are reported as ``APPROVAL.TURN_CLOSURE`` violations.
"""

from __future__ import annotations

from .loginvariants import InvariantViolationError, register


@register(
    "APPROVAL.TURN_CLOSURE",
    "APPROVAL_DECIDED must follow APPROVAL_ASKED with the same tool_call_id, "
    "and both must live inside the same TURN_START/TURN_END window",
)
def _check_approval_turn_closure(events: list) -> None:
    open_asks: dict[str, int] = {}  # tool_call_id -> index of TURN_START
    last_turn_start: int = -1  # index of most recent TURN_START seen
    for idx, event in enumerate(events):
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == "TURN_START":
            last_turn_start = idx
            # Invalidate any unmatched asks — they straddle the turn boundary.
            open_asks = {}
        elif etype == "TURN_END":
            if open_asks:
                raise InvariantViolationError(
                    code="APPROVAL.TURN_CLOSURE",
                    message=f"TURN_END at idx {idx} closes with open asks: {sorted(open_asks)}",
                )
        elif etype == "APPROVAL_ASKED":
            tc_id = event.payload.get("tool_call_id", "")
            if tc_id:
                open_asks[tc_id] = last_turn_start
        elif etype == "APPROVAL_DECIDED":
            tc_id = event.payload.get("tool_call_id", "")
            asked_idx = open_asks.pop(tc_id, None)
            if asked_idx is None:
                raise InvariantViolationError(
                    code="APPROVAL.TURN_CLOSURE",
                    message=f"APPROVAL_DECIDED for tool_call_id={tc_id!r} without matching APPROVAL_ASKED",
                )
