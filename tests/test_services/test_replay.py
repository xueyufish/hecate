"""Unit tests for the 8.20 replay services.

Covers assembler (partitioning, payload truncation, body association,
guardrail derivation), and state inspector (commit-point fallback, fold).
"""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType, InMemoryEventStore
from hecate.services.replay.assembler import (
    REPLAY_PAYLOAD_PREVIEW_CHARS,
    assemble_timeline,
    derive_guardrail_blocks,
    derive_message_bodies,
)
from hecate.services.replay.state_inspector import inspect_at_version


def _make_event(
    session_id: uuid.UUID,
    *,
    version: int,
    superstep: int,
    event_type: EventType,
    payload: dict | None = None,
    trace_id: str | None = "trace-A",
    node_id: str | None = None,
    log_schema_version: int | None = CURRENT_LOG_SCHEMA_VERSION,
) -> Event:
    if payload is None:
        payload = {}
    if log_schema_version is not None:
        payload = {**payload, "log_schema_version": log_schema_version}
    return Event(
        session_id=session_id,
        superstep=superstep,
        event_type=event_type,
        node_id=node_id,
        trace_id=trace_id,
        version=version,
        payload=payload,
    )


# -- assembler.timeline --


@pytest.mark.asyncio
async def test_assemble_partitions_by_trace_id() -> None:
    sid = uuid.uuid4()
    events = [
        _make_event(sid, version=1, superstep=1, event_type=EventType.NODE_START, node_id="A", trace_id="t1"),
        _make_event(sid, version=2, superstep=1, event_type=EventType.NODE_END, node_id="A", trace_id="t1"),
        _make_event(sid, version=3, superstep=2, event_type=EventType.NODE_START, node_id="A", trace_id="t2"),
        _make_event(sid, version=4, superstep=2, event_type=EventType.NODE_END, node_id="A", trace_id="t2"),
    ]
    out = assemble_timeline(events)
    assert [seg["trace_id"] for seg in out["traces"]] == ["t1", "t2"]
    assert [seg["event_count"] for seg in out["traces"]] == [2, 2]
    assert out["unattributed"] == []
    assert out["next_cursor"] is None  # limit not hit


@pytest.mark.asyncio
async def test_unattributed_bucket_for_none_and_zero_trace_ids() -> None:
    sid = uuid.uuid4()
    zero = "0" * 32
    events = [
        _make_event(sid, version=1, superstep=1, event_type=EventType.CUSTOM, trace_id=None),
        _make_event(sid, version=2, superstep=1, event_type=EventType.CUSTOM, trace_id=zero),
        _make_event(sid, version=3, superstep=1, event_type=EventType.NODE_START, trace_id="t1", node_id="A"),
    ]
    out = assemble_timeline(events)
    assert len(out["unattributed"]) == 2
    assert len(out["traces"]) == 1
    assert out["traces"][0]["trace_id"] == "t1"


@pytest.mark.asyncio
async def test_payload_truncation_for_long_messages() -> None:
    sid = uuid.uuid4()
    long_messages = ["x" * (REPLAY_PAYLOAD_PREVIEW_CHARS + 50)] * 3
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.LLM_REQUEST,
            payload={"messages": long_messages, "model": "m"},
            trace_id="t1",
        ),
    ]
    summary = assemble_timeline(events)
    assert summary["payload_truncated"] is True
    rendered = summary["traces"][0]["events"][0]["payload"]["messages"]
    assert isinstance(rendered, str)
    assert len(rendered) == REPLAY_PAYLOAD_PREVIEW_CHARS


@pytest.mark.asyncio
async def test_detail_mode_returns_full_payload() -> None:
    sid = uuid.uuid4()
    long = "x" * 500
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.LLM_REQUEST,
            payload={"messages": long},
            trace_id="t1",
        ),
    ]
    detail = assemble_timeline(events, detail=True)
    assert detail["payload_truncated"] is False
    assert detail["traces"][0]["events"][0]["payload"]["messages"] == long


@pytest.mark.asyncio
async def test_limit_and_cursor_pagination() -> None:
    sid = uuid.uuid4()
    events = [
        _make_event(sid, version=i, superstep=1, event_type=EventType.CUSTOM, trace_id="t1") for i in range(1, 11)
    ]
    page1 = assemble_timeline(events, from_version=0, limit=4)
    assert sum(s["event_count"] for s in page1["traces"]) == 4
    assert page1["next_cursor"] == 5

    page2 = assemble_timeline(events, from_version=page1["next_cursor"], limit=4)
    assert sum(s["event_count"] for s in page2["traces"]) == 4
    assert page2["next_cursor"] == 9

    last = assemble_timeline(events, from_version=page2["next_cursor"], limit=4)
    assert sum(s["event_count"] for s in last["traces"]) == 2
    assert last["next_cursor"] is None  # limit not exhausted


@pytest.mark.asyncio
async def test_limit_clamped_to_max() -> None:
    sid = uuid.uuid4()
    events = [_make_event(sid, version=i, superstep=1, event_type=EventType.CUSTOM, trace_id="t1") for i in range(1, 4)]
    out = assemble_timeline(events, limit=10000)
    # Exceeds MAX (500); result still bounded; not asserting 500 here, just no crash.
    assert "traces" in out


# -- assembler.body association --


@pytest.mark.asyncio
async def test_derive_message_bodies_links_llm_response_to_messages_write() -> None:
    sid = uuid.uuid4()
    msgs = [{"role": "assistant", "content": "hi"}]
    events = [
        _make_event(sid, version=1, superstep=1, event_type=EventType.NODE_START, node_id="A", trace_id="t1"),
        _make_event(
            sid,
            version=2,
            superstep=1,
            event_type=EventType.LLM_REQUEST,
            payload={"model": "m"},
            trace_id="t1",
            node_id="A",
        ),
        _make_event(
            sid,
            version=3,
            superstep=1,
            event_type=EventType.LLM_RESPONSE,
            payload={"model": "m", "response_length": 2},
            trace_id="t1",
            node_id="A",
        ),
        _make_event(
            sid,
            version=4,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": msgs},
            trace_id="t1",
            node_id="A",
        ),
        _make_event(sid, version=5, superstep=1, event_type=EventType.NODE_END, node_id="A", trace_id="t1"),
    ]
    bodies = derive_message_bodies(events, ["t1"])
    assert len(bodies) == 1
    bodies_list = next(iter(bodies.values()))
    assert bodies_list == [msgs]


@pytest.mark.asyncio
async def test_derive_message_bodies_handles_tool_result() -> None:
    sid = uuid.uuid4()
    tool_msg = {"role": "tool", "content": "ok", "tool_call_id": "1"}
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.TOOL_RESULT,
            payload={"result_length": 2},
            trace_id="t1",
            node_id="B",
        ),
        _make_event(
            sid,
            version=2,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": tool_msg},
            trace_id="t1",
            node_id="B",
        ),
    ]
    bodies = derive_message_bodies(events, ["t1"])
    assert len(bodies) == 1
    bodies_list = next(iter(bodies.values()))
    assert bodies_list == [tool_msg]


# -- assembler.guardrail derivation --


@pytest.mark.asyncio
async def test_guardrail_derivation_for_tool_block_prefix() -> None:
    sid = uuid.uuid4()
    block_msg = {"role": "tool", "is_error": True, "content": "Tool blocked: PII detected"}
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": block_msg},
            trace_id="t1",
        ),
    ]
    entries = derive_guardrail_blocks(events)
    assert len(entries) == 1
    assert entries[0]["reason"] == "PII detected"
    assert entries[0]["block_type"] == "tool blocked"


@pytest.mark.asyncio
async def test_guardrail_derivation_ignores_non_block_messages() -> None:
    sid = uuid.uuid4()
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={
                "channel": "messages",
                "value": {"role": "tool", "is_error": True, "content": "Tool timeout (network)"},
            },
            trace_id="t1",
        ),
        _make_event(
            sid,
            version=2,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": {"role": "assistant", "content": "ok"}},
            trace_id="t1",
        ),
    ]
    entries = derive_guardrail_blocks(events)
    assert entries == []


# -- state_inspector --


@pytest.mark.asyncio
async def test_state_inspector_folds_to_commit_point() -> None:
    sid = uuid.uuid4()
    msg = {"role": "user", "content": "hi"}
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [msg]},
        ),
        _make_event(sid, version=2, superstep=1, event_type=EventType.STEP_END),
        _make_event(
            sid,
            version=3,
            superstep=2,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [{"role": "assistant", "content": "reply"}]},
        ),
        _make_event(sid, version=4, superstep=2, event_type=EventType.STEP_END),
    ]
    # Request past STEP_END v2 but before STEP_END v4 -> should fall back to v2.
    out = inspect_at_version(events, at_version=3)
    assert out["effective_version"] == 2
    assert out["fell_back"] is True
    assert out["commit_points"] == [2, 4]
    assert out["messages"] == [msg]


@pytest.mark.asyncio
async def test_state_inspector_exact_commit_point() -> None:
    sid = uuid.uuid4()
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [{"role": "user", "content": "x"}]},
        ),
        _make_event(sid, version=2, superstep=1, event_type=EventType.STEP_END),
    ]
    out = inspect_at_version(events, at_version=2)
    assert out["effective_version"] == 2
    assert out["fell_back"] is False


@pytest.mark.asyncio
async def test_state_inspector_empty_when_before_first_commit() -> None:
    sid = uuid.uuid4()
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [{"role": "user", "content": "x"}]},
        ),
    ]
    out = inspect_at_version(events, at_version=1)
    assert out["effective_version"] == 0
    assert out["fell_back"] is True
    assert out["messages"] == []


@pytest.mark.asyncio
async def test_state_inspector_nonreplayable_prefix_raises() -> None:
    from hecate.services.observability.logfold import NonReplayablePrefix

    sid = uuid.uuid4()
    events = [
        _make_event(
            sid,
            version=1,
            superstep=1,
            event_type=EventType.CHANNEL_WRITE,
            payload={"channel": "messages", "value": [{"role": "user", "content": "x"}]},
            log_schema_version=1,  # below CURRENT
        ),
        _make_event(
            sid,
            version=2,
            superstep=1,
            event_type=EventType.STEP_END,
            log_schema_version=1,  # below CURRENT
        ),
    ]
    with pytest.raises(NonReplayablePrefix):
        inspect_at_version(events, at_version=2)


@pytest.mark.asyncio
async def test_state_inspector_session_wrapper_via_inmemory_store() -> None:
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    await store.append(_make_event(sid, version=1, superstep=1, event_type=EventType.STEP_END))
    from hecate.services.replay.state_inspector import inspect_session_at

    out = await inspect_session_at(store, sid, at_version=1)
    assert out["effective_version"] == 1
    assert out["fell_back"] is False
