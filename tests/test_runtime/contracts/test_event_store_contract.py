"""EventStore contract — production impl vs contract fake.

Proves that ToolWorker behaves identically against either EventStore
implementation: replacement requires zero Worker modification. New
EventStore implementations must register themselves in
``EVENT_STORE_FACTORIES``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore
from hecate.runtime.workers.tool_worker import ToolWorker, get_tool_receipt


class FakeEventStore:
    """Hand-written second implementation of the EventStore contract.

    Deliberately NOT derived from InMemoryEventStore: it shares only the
    contract surface the Workers use (append / append_batch / get_events).
    """

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, list[Event]] = {}

    async def append(self, event: Event) -> uuid.UUID:
        events = self._store.setdefault(event.session_id, [])
        versioned = Event(
            session_id=event.session_id,
            superstep=event.superstep,
            event_type=event.event_type,
            node_id=event.node_id,
            id=event.id,
            timestamp=event.timestamp,
            payload=event.payload,
            trace_id=event.trace_id,
            version=len(events) + 1,
        )
        events.append(versioned)
        return versioned.id

    async def append_batch(self, events: list[Event]) -> list[uuid.UUID]:
        return [await self.append(e) for e in events]

    async def get_events(self, session_id: uuid.UUID, from_version: int = 0) -> list[Event]:
        return [e for e in self._store.get(session_id, []) if e.version >= from_version]


EVENT_STORE_FACTORIES: dict[str, Any] = {
    "in_memory": InMemoryEventStore,
    "fake": FakeEventStore,
}


class _StubPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        return {"executed": True}

    async def tool_execute_sandbox(self, name, args, context=None):
        self.calls.append((name, args))
        return {"executed": True}

    async def create_span(self, *args, **kwargs):
        class _CM:
            span_id = "span"

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


def _payload(call_id: str, name: str) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps({"query": "x"})},
                    }
                ],
            },
        ]
    }


def _execution_context(session_id: uuid.UUID) -> dict:
    return {"session_id": session_id, "superstep": 0, "trace_id": None}


@pytest.fixture(params=list(EVENT_STORE_FACTORIES), ids=list(EVENT_STORE_FACTORIES))
def event_store(request) -> Any:
    return EVENT_STORE_FACTORIES[request.param]()


@pytest.mark.asyncio
async def test_contract_append_and_read_roundtrip(event_store) -> None:
    session_id = uuid.uuid4()
    await event_store.append(
        Event(session_id=session_id, superstep=0, event_type=EventType.TOOL_CALL, payload={"k": "v"})
    )
    events = await event_store.get_events(session_id)
    assert len(events) == 1
    assert events[0].payload == {"k": "v"}
    assert events[0].version == 1


@pytest.mark.asyncio
async def test_contract_worker_receipt_flow_identical(event_store) -> None:
    """ToolWorker produces the same receipt flow on either store."""
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=event_store)

    await worker.execute("tools", {}, _payload("call-1", "web_search"), _execution_context(session_id))

    events = await event_store.get_events(session_id)
    calls = [e for e in events if e.event_type is EventType.TOOL_CALL]
    receipts = [e for e in events if e.event_type is EventType.TOOL_RESULT]
    assert len(calls) == 1 and len(receipts) == 1
    assert receipts[0].payload["execution_id"] == calls[0].payload["execution_id"]
    assert receipts[0].payload["status"] == "succeeded"

    # Receipt query works on either store.
    execution_id = calls[0].payload["execution_id"]
    receipt = await get_tool_receipt(event_store, session_id, execution_id)
    assert receipt is not None and receipt["status"] == "succeeded"


@pytest.mark.asyncio
async def test_contract_worker_resume_skips_on_either_store(event_store) -> None:
    """Resume replay (same session + tool_call_id) skips re-execution on
    both implementations — swapability covers the recovery path too."""
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=event_store)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search"), ctx)
    replay = await worker.execute("tools", {}, _payload("call-1", "web_search"), ctx)

    assert len(port.calls) == 1
    assert "[recovered]" in replay.channel_updates["messages"][0]["content"]
