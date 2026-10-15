"""Unit tests for engine-event-to-contract mapping (step5d).

Covers the ``platform-entry-execution`` event mapping requirement:
envelopes carry task/run refs with monotonic sequences, original engine
events survive as backend details, client-safe payload projection keeps
engine internals out of protocol rendering, and tool pairing reports
leftovers instead of dropping them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from hecate.contracts.execution.events import EventKind
from hecate.contracts.execution.references import run_ref, task_ref
from hecate.execution.entry_events import RunEventMapper, validate_tool_pairing

_TASK = task_ref("hecate", str(uuid.uuid4()))
_RUN = run_ref("hecate", str(uuid.uuid4()))


class _FakeEvent:
    """Minimal engine event-log record shape (event_type + payload)."""

    def __init__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_type = event_type
        self.payload = payload


@dataclass
class _StreamChunk:
    type: str
    content: str | None = None
    state: dict = field(default_factory=dict)


async def test_mapping_assigns_task_run_refs_and_monotonic_sequence() -> None:
    mapper = RunEventMapper(_TASK, _RUN)
    first = await mapper.map_stream_event({"type": "message", "content": "hello"})
    second = await mapper.map_stream_event({"type": "message", "content": "world"})
    assert first.task_ref == _TASK
    assert first.run_ref == _RUN
    assert first.kind is EventKind.EVENT
    assert first.payload_schema_ref
    assert second.source_sequence == first.source_sequence + 1
    assert first.payload == {"type": "message", "content": "hello"}


async def test_original_engine_event_is_preserved_as_backend_detail() -> None:
    mapper = RunEventMapper(_TASK, _RUN)
    engine_event = {
        "type": "values",
        "state": {"messages": [], "_route": "llm", "_session_id": "s-1", "suggested_questions": ["q1"]},
    }
    envelope = await mapper.map_stream_event(engine_event)
    assert envelope.extra["backend_detail"] == engine_event


async def test_values_payload_projection_keeps_engine_channels_out() -> None:
    """Internal state channel names never reach the client-safe payload."""
    mapper = RunEventMapper(_TASK, _RUN)
    envelope = await mapper.map_stream_event(
        {"type": "values", "state": {"_route": "check_tools", "_has_tool_call": True, "suggested_questions": ["q"]}}
    )
    assert envelope.payload == {"type": "values", "suggested_questions": ["q"]}
    # The raw state stays inspectable for the platform only.
    assert envelope.extra["backend_detail"]["state"]["_route"] == "check_tools"


async def test_envelopes_round_trip_through_dict() -> None:
    from hecate.contracts.execution.events import EventEnvelope

    mapper = RunEventMapper(_TASK, _RUN)
    envelope = await mapper.map_stream_event({"type": "message", "content": "x"})
    restored = EventEnvelope.from_dict(envelope.to_dict())
    assert restored.source_sequence == envelope.source_sequence
    assert restored.task_ref == _TASK
    assert restored.extra["backend_detail"] == envelope.extra["backend_detail"]


async def test_read_events_pages_by_cursor_without_gaps() -> None:
    """The mapped stream pages by run cursor: consecutive, resumable."""
    mapper = RunEventMapper(_TASK, _RUN)
    for index in range(5):
        await mapper.map_stream_event({"type": "message", "content": str(index)})

    first = await mapper.read_events(limit=2)
    assert [e.source_sequence for e in first.events] == [0, 1]
    assert first.has_more and first.next_cursor == "1"

    second = await mapper.read_events(cursor=first.next_cursor, limit=2)
    assert [e.source_sequence for e in second.events] == [2, 3]
    assert second.has_more and second.next_cursor == "3"

    tail = await mapper.read_events(cursor=second.next_cursor, limit=2)
    assert [e.source_sequence for e in tail.events] == [4]
    assert not tail.has_more

    # Cursor at the end: empty page, cursor held.
    done = await mapper.read_events(cursor=tail.next_cursor, limit=2)
    assert not done.events and not done.has_more


async def test_read_events_unknown_cursor_fails_closed() -> None:
    mapper = RunEventMapper(_TASK, _RUN)
    await mapper.map_stream_event({"type": "message", "content": "x"})
    import pytest

    with pytest.raises(ValueError):
        await mapper.read_events(cursor="not-a-number")


def test_tool_pairing_reports_paired_and_leftover_calls() -> None:
    events = [
        _FakeEvent("TOOL_CALL", {"execution_id": "e1", "tool_call_id": "tc1", "tool_name": "search"}),
        _FakeEvent("TOOL_RESULT", {"execution_id": "e1", "status": "succeeded"}),
        _FakeEvent("TOOL_CALL", {"execution_id": "e2", "tool_call_id": "tc2", "tool_name": "calc"}),
        _FakeEvent("TOOL_RESULT", {"execution_id": "e3", "status": "succeeded"}),
    ]
    report = validate_tool_pairing(events)
    assert [(p["execution_id"], p["tool_name"]) for p in report.paired] == [("e1", "search")]
    assert [c["execution_id"] for c in report.unpaired_calls] == ["e2"]
    assert [r["execution_id"] for r in report.unpaired_results] == ["e3"]
    assert not report.ok


def test_tool_pairing_all_paired_is_ok() -> None:
    events = [
        _FakeEvent("TOOL_CALL", {"execution_id": "e1", "tool_name": "search"}),
        _FakeEvent("TOOL_RESULT", {"execution_id": "e1", "status": "succeeded"}),
    ]
    report = validate_tool_pairing(events)
    assert report.ok
    assert report.paired[0]["status"] == "succeeded"


def test_tool_pairing_ignores_non_tool_events() -> None:
    events = [
        _FakeEvent("NODE_START", {"node": "llm"}),
        _FakeEvent("TOOL_CALL", {"tool_call_id": "no-execution-id"}),
    ]
    report = validate_tool_pairing(events)
    assert report.ok
    assert not report.paired


async def test_terminal_envelope_carries_state_category_and_safe_error() -> None:
    mapper = RunEventMapper(_TASK, _RUN)
    await mapper.map_stream_event({"type": "message", "content": "partial"})
    terminal = await mapper.write_terminal_envelope(
        state="failed", stop_category="iteration_limit", error_type="MaxSuperstepsError"
    )
    assert terminal.kind is EventKind.TERMINAL
    assert terminal.payload_schema_ref == "hecate.entry.terminal/0"
    assert terminal.payload["state"] == "failed"
    assert terminal.payload["stop_category"] == "iteration_limit"
    assert terminal.payload["error"] == {"type": "MaxSuperstepsError"}
    assert terminal.source_sequence == 1
    # The terminal envelope pages together with the stream events.
    page = await mapper.read_events()
    assert [e.kind for e in page.events] == [EventKind.EVENT, EventKind.TERMINAL]


async def test_terminal_envelope_success_carries_artifacts_only_when_present() -> None:
    mapper = RunEventMapper(_TASK, _RUN)
    ok = await mapper.write_terminal_envelope(
        state="succeeded", artifacts=[{"kind": "artifact", "issuer_domain": "hecate", "id": "a1"}]
    )
    assert ok.payload == {
        "state": "succeeded",
        "artifacts": [{"kind": "artifact", "issuer_domain": "hecate", "id": "a1"}],
    }
    assert await mapper.write_terminal_envelope(state="succeeded") is ok
    assert len((await mapper.read_events()).events) == 1
    bare = await RunEventMapper(_TASK, _RUN).write_terminal_envelope(state="cancelled", stop_category="cancelled")
    assert bare.payload == {"state": "cancelled", "stop_category": "cancelled"}


def test_extract_declared_artifacts_accepts_only_platform_artifact_refs() -> None:
    from hecate.execution.entry_events import extract_declared_artifacts

    accepted, rejected = extract_declared_artifacts(
        [
            {"kind": "artifact", "issuer_domain": "hecate", "id": "a1"},
            {"kind": "artifact", "issuer_domain": "foreign", "id": "a2"},
            {"kind": "run", "issuer_domain": "hecate", "id": "r1"},
            {"kind": "artifact", "issuer_domain": "hecate"},
            "junk",
        ],
        issuer_domain="hecate",
        verified_artifact_ids={"a1"},
    )
    assert accepted == [{"kind": "artifact", "issuer_domain": "hecate", "id": "a1"}]
    reasons = {r["reason"] for r in rejected}
    assert any("foreign issuer" in r for r in reasons)
    assert any("unsupported reference kind" in r for r in reasons)
    assert any("issuer_domain and id" in r for r in reasons)
    assert any("must be an object" in r for r in reasons)


def test_extract_declared_artifacts_tolerates_absent_or_malformed_container() -> None:
    from hecate.execution.entry_events import extract_declared_artifacts

    assert extract_declared_artifacts(None, issuer_domain="hecate") == ([], [])
    accepted, rejected = extract_declared_artifacts({"kind": "artifact"}, issuer_domain="hecate")
    assert accepted == []
    assert rejected and "must be a list" in rejected[0]["reason"]
