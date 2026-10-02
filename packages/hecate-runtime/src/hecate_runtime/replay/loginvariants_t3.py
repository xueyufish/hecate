"""T3 invariants — MONOTONIC.DENIAL (T3.6).

A log that records a denial for a tool call MUST NOT also contain a
``TOOL_CALL`` execution event for the same ``tool_call_id``. Resurrection
is a bug.

Denials can come from:
    - ``CHANNEL_WRITE_REJECTED`` (the guard/policy rejected the write
      that would have run the tool).
    - ``APPROVAL_DECIDED`` with ``approved=False``.
"""

from __future__ import annotations

from .loginvariants import InvariantViolationError, register


@register(
    "MONOTONIC.DENIAL",
    "A denied tool call must never be followed by a TOOL_CALL execution "
    "for the same tool_call_id (resurrection is a bug)",
)
def _check_monotonic_denial(events: list) -> None:
    denied_ids: set[str] = set()
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == "CHANNEL_WRITE_REJECTED":
            tc_id = event.payload.get("tool_call_id", "") or event.payload.get("source_tool_call_id", "")
            if tc_id:
                denied_ids.add(tc_id)
        elif etype == "APPROVAL_DECIDED":
            if event.payload.get("approved") is False:
                tc_id = event.payload.get("tool_call_id", "")
                if tc_id:
                    denied_ids.add(tc_id)
        elif etype == "TOOL_CALL":
            tc_id = event.payload.get("tool_call_id", "")
            if tc_id and tc_id in denied_ids:
                raise InvariantViolationError(
                    code="MONOTONIC.DENIAL",
                    message=(
                        f"tool_call_id={tc_id!r} was denied but a later TOOL_CALL "
                        f"execution appeared in the log; resurrection is a bug"
                    ),
                )
