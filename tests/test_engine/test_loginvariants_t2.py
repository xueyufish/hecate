"""T2.2 — approval pair turn-closure invariant."""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType
from hecate.services.observability import loginvariants_t2  # noqa: F401  side-effect: register
from hecate.services.observability.loginvariants import InvariantViolationError, run_all


def _ev(sid, ss, etype, **payload):
    full = {"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, **payload}
    return Event(session_id=sid, superstep=ss, event_type=etype, payload=full)


def test_decided_without_asked_violates():
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.TURN_START),
        _ev(sid, 1, EventType.APPROVAL_DECIDED, tool_call_id="tc", approved=True),
        _ev(sid, 1, EventType.TURN_END),
    ]
    with pytest.raises(InvariantViolationError) as exc:
        run_all(events)
    assert exc.value.code == "APPROVAL.TURN_CLOSURE"


def test_turn_end_with_open_asks_violates():
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.TURN_START),
        _ev(sid, 1, EventType.APPROVAL_ASKED, tool_call_id="tc"),
        _ev(sid, 1, EventType.TURN_END),
    ]
    with pytest.raises(InvariantViolationError) as exc:
        run_all(events)
    assert exc.value.code == "APPROVAL.TURN_CLOSURE"


def test_balanced_pair_within_turn_passes():
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.TURN_START),
        _ev(sid, 1, EventType.APPROVAL_ASKED, tool_call_id="tc"),
        _ev(sid, 1, EventType.APPROVAL_DECIDED, tool_call_id="tc", approved=True),
        _ev(sid, 1, EventType.TURN_END),
    ]
    run_all(events)


def test_ask_straddling_turn_boundary_violates():
    """An APPROVAL_ASKED whose TURN_START has been "consumed" by another
    TURN_START is invalidated — its pair cannot complete."""
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.TURN_START),
        _ev(sid, 1, EventType.APPROVAL_ASKED, tool_call_id="tc"),
        _ev(sid, 1, EventType.TURN_START),
        _ev(sid, 1, EventType.APPROVAL_DECIDED, tool_call_id="tc", approved=True),
        _ev(sid, 1, EventType.TURN_END),
    ]
    with pytest.raises(InvariantViolationError) as exc:
        run_all(events)
    assert exc.value.code == "APPROVAL.TURN_CLOSURE"
