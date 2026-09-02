"""T3.5 / T3.6 — CHANNEL_WRITE_REJECTED emission + MONOTONIC.DENIAL invariant."""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType
from hecate.runtime.replay import (
    loginvariants_t2,  # noqa: F401  side-effect
    loginvariants_t3,  # noqa: F401  side-effect
)
from hecate.runtime.replay.loginvariants import InvariantViolationError, run_all


def _ev(sid, ss, etype, **payload):
    full = {"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, **payload}
    return Event(session_id=sid, superstep=ss, event_type=etype, payload=full)


def test_channel_write_rejected_emitted_and_fold_skipped():
    """T3.5 — a CHANNEL_WRITE_REJECTED event is appended by the worker; the
    fold path does NOT apply it to channel state.
    """
    from hecate.runtime.channel import ChannelDef, ChannelManager, ChannelType
    from hecate.runtime.replay.logfold import fold_session

    cm = ChannelManager()
    cm.register("messages", ChannelDef(type=ChannelType.TOPIC, default=[]))

    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value="hi"),
        _ev(sid, 1, EventType.CHANNEL_WRITE_REJECTED, channel="messages", value="bye", reason="deny"),
        _ev(sid, 1, EventType.STEP_END),
    ]
    last_version = fold_session(cm, iter(events))
    # Rejected write did NOT pollute channel state.
    assert cm.read("messages") == ["hi"]
    # The fold reached the end (last_version is the largest event.version
    # observed; here both events share superstep/version default).
    assert last_version >= 0


def test_monotonic_denial_violation_on_resurrection():
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.CHANNEL_WRITE_REJECTED, tool_call_id="tc-x", reason="deny"),
        _ev(sid, 2, EventType.TOOL_CALL, tool_call_id="tc-x", tool_name="bash"),
    ]
    with pytest.raises(InvariantViolationError) as exc:
        run_all(events)
    assert exc.value.code == "MONOTONIC.DENIAL"
    assert "tc-x" in str(exc.value)


def test_monotonic_denial_clean_log_passes():
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.TOOL_CALL, tool_call_id="tc-ok", tool_name="bash"),
        _ev(sid, 1, EventType.TOOL_RESULT, tool_call_id="tc-ok", tool_name="bash"),
    ]
    run_all(events)


def test_approval_denied_blocks_subsequent_tool_call():
    """A denied approval followed by a TOOL_CALL for the same id is a
    resurrection. The event stream carries a well-formed asked/decided pair
    (turn-enclosed) so the TURN_CLOSURE invariant does not fire first —
    this test pins MONOTONIC.DENIAL specifically.
    """
    sid = uuid.uuid4()
    events = [
        _ev(sid, 1, EventType.TURN_START),
        _ev(sid, 1, EventType.APPROVAL_ASKED, tool_call_id="tc-app", tool_name="bash"),
        _ev(sid, 1, EventType.APPROVAL_DECIDED, tool_call_id="tc-app", approved=False),
        _ev(sid, 2, EventType.TURN_END),
        _ev(sid, 2, EventType.TOOL_CALL, tool_call_id="tc-app", tool_name="bash"),
    ]
    with pytest.raises(InvariantViolationError) as exc:
        run_all(events)
    assert exc.value.code == "MONOTONIC.DENIAL"
