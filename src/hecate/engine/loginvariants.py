"""Runtime invariant registry for log-derived execution state.

Each registered check is invoked during fold (relation checks) or on event
append (cheap local checks). Violations raise :class:`InvariantViolation`
with a stable code; callers in the engine convert this into a fail-stop
abort (see design D3 mechanism 3).

A violation is a bug signal: state has diverged from what the log says.
Recovery is "discard the in-memory state and re-fold from the log", not
"hot-fix the in-memory state" — the latter would mask the bug and produce
object-identity issues with worker-held deep copies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InvariantViolationError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


InvariantViolation = InvariantViolationError  # backward-compat alias


@dataclass(frozen=True)
class _Check:
    code: str
    description: str
    fn: Callable[[Iterable[Any]], None]


_REGISTRY: list[_Check] = []


def register(code: str, description: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Companion-style registration decorator (covariant over events)."""

    def deco(fn: Callable[..., None]) -> Callable[..., None]:
        _REGISTRY.append(_Check(code=code, description=description, fn=fn))
        return fn

    return deco


def list_registered() -> list[tuple[str, str]]:
    return [(c.code, c.description) for c in _REGISTRY]


def run_all(events: Iterable[Any]) -> None:
    """Run all registered checks. First violation raises."""
    materialized = list(events)
    for check in _REGISTRY:
        check.fn(materialized)


# --- Built-in checks ---


@register("STEP.BOUNDARY", "Every superstep with writes must end with STEP_END or INTERRUPT")
def _check_step_boundary(events: list[Any]) -> None:
    supersteps_with_writes: dict[int, bool] = {}
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == "CHANNEL_WRITE":
            supersteps_with_writes[event.superstep] = False
        elif etype in {"STEP_END", "INTERRUPT"}:
            supersteps_with_writes[event.superstep] = True
    for superstep, closed in supersteps_with_writes.items():
        if not closed:
            raise InvariantViolation(
                code="STEP.BOUNDARY",
                message=f"superstep {superstep} has CHANNEL_WRITE without STEP_END/INTERRUPT",
            )


@register("TOOL.PAIRING", "Every TOOL_CALL must be paired with a TOOL_RESULT before STEP_END")
def _check_tool_pairing(events: list[Any]) -> None:
    pending: dict[str, int] = {}
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == "TOOL_CALL":
            call_id = event.payload.get("tool_call_id", "")
            if call_id:
                pending[call_id] = event.superstep
        elif etype == "TOOL_RESULT":
            pending.pop(event.payload.get("tool_call_id", ""), None)
        elif etype in {"STEP_END", "INTERRUPT"} and pending:
            raise InvariantViolation(
                code="TOOL.PAIRING",
                message=f"superstep {event.superstep} closed with pending tool calls: {sorted(pending)}",
            )


@register("DISPATCH.TREE", "Every SUBGRAPH_START must have a SUBGRAPH_END on the same session")
def _check_dispatch_tree(events: list[Any]) -> None:
    open_starts = 0
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == "SUBGRAPH_START":
            open_starts += 1
        elif etype == "SUBGRAPH_END":
            open_starts -= 1
            if open_starts < 0:
                raise InvariantViolation(
                    code="DISPATCH.TREE",
                    message="SUBGRAPH_END without matching SUBGRAPH_START",
                )
    if open_starts != 0:
        raise InvariantViolation(
            code="DISPATCH.TREE",
            message=f"unbalanced subgraph dispatch: {open_starts} open SUBGRAPH_STARTs",
        )
