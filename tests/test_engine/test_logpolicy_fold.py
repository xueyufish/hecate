"""Tests for engine/logpolicy.py and engine/logfold.py and engine/loginvariants.py."""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.channel import ChannelManager
from hecate.engine.eventstore import (
    CURRENT_LOG_SCHEMA_VERSION,
    Event,
    EventStore,
    EventType,
    InMemoryEventStore,
)
from hecate.engine.logfold import (
    NonReplayablePrefixError,
    derive_messages,
    fold_session,
    fold_session_from_store,
)
from hecate.engine.loginvariants import InvariantViolation, list_registered, run_all
from hecate.engine.logpolicy import should_log_channel
from hecate.engine.types import ChannelDef, ChannelType

# --- LogPolicy ---


def test_logpolicy_default_value_channels_logged():
    assert should_log_channel("messages") is True
    assert should_log_channel("my_custom_state") is True


def test_logpolicy_fanout_subchannels_excluded():
    assert should_log_channel("_fanout__node1__branch_a") is False


def test_logpolicy_underscore_control_excluded():
    assert should_log_channel("_session_id") is False
    assert should_log_channel("_tools") is False
    assert should_log_channel("_agent_state") is False


def test_logpolicy_sys_control_excluded():
    assert should_log_channel("sys.execution_mode") is False
    assert should_log_channel("sys.dialogue_count") is False


def test_logpolicy_resume_value_excluded():
    assert should_log_channel("_resume_value") is False


def test_logpolicy_route_explicitly_included():
    assert should_log_channel("_route") is True


# --- Fold machine ---


def _make_manager(*channels: tuple[str, ChannelType]) -> ChannelManager:
    cm = ChannelManager()
    for name, ctype in channels:
        cm.register(name, ChannelDef(type=ctype))
    return cm


def _make_event(
    session_id: uuid.UUID,
    superstep: int,
    event_type: EventType,
    **payload: object,
) -> Event:
    full_payload: dict[str, object] = {"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, **payload}
    return Event(
        session_id=session_id,
        superstep=superstep,
        event_type=event_type,
        payload=full_payload,
    )


def test_fold_rebuilds_topic_channel_from_chained_writes():
    sid = uuid.uuid4()
    cm = _make_manager(("messages", ChannelType.TOPIC))
    events = [
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"role": "user"}),
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"role": "assistant"}),
        _make_event(sid, 1, EventType.STEP_END),
    ]
    fold_session(cm, iter(events))
    assert cm.read("messages") == [{"role": "user"}, {"role": "assistant"}]


def test_fold_rejected_writes_are_skipped():
    sid = uuid.uuid4()
    cm = _make_manager(("messages", ChannelType.TOPIC))
    events = [
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"role": "user"}),
        _make_event(sid, 1, EventType.CHANNEL_WRITE_REJECTED, channel="messages", value={"role": "evil"}),
        _make_event(sid, 1, EventType.STEP_END),
    ]
    fold_session(cm, iter(events))
    assert cm.read("messages") == [{"role": "user"}]


def test_fold_old_schema_prefix_raises_non_replayable():
    sid = uuid.uuid4()
    cm = _make_manager(("messages", ChannelType.TOPIC))
    legacy = Event(
        session_id=sid,
        superstep=1,
        event_type=EventType.CHANNEL_WRITE,
        payload={"channel": "messages", "value": {"role": "user"}},
    )
    with pytest.raises(NonReplayablePrefixError):
        fold_session(cm, iter([legacy]))


async def test_fold_from_store_round_trips_through_in_memory_store():
    sid = uuid.uuid4()
    store: EventStore = InMemoryEventStore()
    await store.append_batch(
        [
            _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"role": "user"}),
            _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"role": "assistant"}),
            _make_event(sid, 1, EventType.STEP_END),
        ]
    )
    cm = _make_manager(("messages", ChannelType.TOPIC))
    await fold_session_from_store(cm, store, sid)
    assert cm.read("messages") == [{"role": "user"}, {"role": "assistant"}]


def test_derive_messages_returns_messages_channel_view():
    cm = _make_manager(("messages", ChannelType.TOPIC))
    cm.write("messages", {"role": "user"})
    cm.write("messages", {"role": "assistant"})
    assert derive_messages(cm) == [{"role": "user"}, {"role": "assistant"}]


def test_derive_messages_missing_channel_returns_empty():
    cm = _make_manager()
    assert derive_messages(cm) == []


def test_fold_eviction_removes_items_from_list():
    sid = uuid.uuid4()
    cm = _make_manager(("messages", ChannelType.TOPIC))
    events = [
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"i": 0}),
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"i": 1}),
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value={"i": 2}),
        _make_event(sid, 1, EventType.EVICTION, channel="messages", drop_indices=[1]),
        _make_event(sid, 1, EventType.STEP_END),
    ]
    fold_session(cm, iter(events))
    assert cm.read("messages") == [{"i": 0}, {"i": 2}]


# --- LogInvariants ---


def test_invariants_registered_at_import_time():
    codes = {code for code, _ in list_registered()}
    assert "STEP.BOUNDARY" in codes
    assert "TOOL.PAIRING" in codes
    assert "DISPATCH.TREE" in codes


def test_invariants_step_boundary_violation():
    sid = uuid.uuid4()
    events = [
        _make_event(sid, 1, EventType.CHANNEL_WRITE, channel="messages", value=1),
    ]
    with pytest.raises(InvariantViolation) as exc:
        run_all(events)
    assert exc.value.code == "STEP.BOUNDARY"


def test_invariants_tool_pairing_violation():
    sid = uuid.uuid4()
    events = [
        _make_event(sid, 1, EventType.TOOL_CALL, tool_call_id="t1", tool_name="x"),
        _make_event(sid, 1, EventType.STEP_END),
    ]
    with pytest.raises(InvariantViolation) as exc:
        run_all(events)
    assert exc.value.code == "TOOL.PAIRING"


def test_invariants_tool_pairing_balanced():
    sid = uuid.uuid4()
    events = [
        _make_event(sid, 1, EventType.TOOL_CALL, tool_call_id="t1", tool_name="x"),
        _make_event(sid, 1, EventType.TOOL_RESULT, tool_call_id="t1"),
        _make_event(sid, 1, EventType.STEP_END),
    ]
    run_all(events)


def test_invariants_tool_pairing_uses_real_payload_key():
    """TOOL.PAIRING SHALL key on ``payload['tool_call_id']`` to match the
    tool_worker emission shape (one TOOL_CALL per call, flat payload).

    Regression guard for guardrail-upgrade-trio T0.3 — the original invariant
    read nested ``payload['tool_calls'][*]['id']`` and never fired because
    tool_worker never emitted that key.
    """
    sid = uuid.uuid4()
    # Open call, then close — well-formed under the new contract.
    good = [
        _make_event(sid, 1, EventType.TOOL_CALL, tool_call_id="abc", tool_name="bash"),
        _make_event(sid, 1, EventType.TOOL_RESULT, tool_call_id="abc", tool_name="bash"),
        _make_event(sid, 1, EventType.STEP_END),
    ]
    run_all(good)

    # Open call carried over STEP_END — must fire.
    bad = [
        _make_event(sid, 2, EventType.TOOL_CALL, tool_call_id="def", tool_name="bash"),
        _make_event(sid, 2, EventType.STEP_END),
    ]
    with pytest.raises(InvariantViolation) as exc:
        run_all(bad)
    assert exc.value.code == "TOOL.PAIRING"
    assert "def" in str(exc.value)


def test_invariants_dispatch_tree_unbalanced():
    sid = uuid.uuid4()
    events = [
        _make_event(sid, 1, EventType.SUBGRAPH_START, child_session_id=uuid.uuid4()),
    ]
    with pytest.raises(InvariantViolation) as exc:
        run_all(events)
    assert exc.value.code == "DISPATCH.TREE"
