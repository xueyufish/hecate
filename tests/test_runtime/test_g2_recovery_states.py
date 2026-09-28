"""G2 recovery-state semantics: four-state resolution, atomic claim,
argument-digest conflict, and honest success recovery.

Reproductions for the plan's G2 gate: a recorded ``TOOL_CALL`` without a
``TOOL_RESULT`` (crash window), an unreadable event store, a duplicate
worker claiming concurrently, and a same-key argument change must never
lead to a blind re-execution of a side-effecting tool, and a recovered
result must be the real recorded content — never placeholder text.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest

from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore
from hecate.runtime.workers.tool_worker import (
    ToolExecutionState,
    ToolWorker,
    resolve_tool_execution_state,
)


class _StubPort:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"executed": True}

    async def tool_execute_sandbox(self, name, args, context=None):
        self.calls.append((name, args))
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"executed": True}

    async def create_span(self, *args, **kwargs):
        class _CM:
            span_id = "span"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


class _BrokenReadStore(InMemoryEventStore):
    """Healthy writes, failing reads — the "store unavailable" fault."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_reads = False

    async def get_events(self, session_id, from_version=0):
        if self.fail_reads:
            raise RuntimeError("event store unavailable")
        return await super().get_events(session_id, from_version)


class _LockFailureStore(InMemoryEventStore):
    """Event lock acquisition always fails (lease lost / DB busy)."""

    @asynccontextmanager
    async def acquire_event_lock(self, session_id, *, timeout_ms: int = 30000):
        raise TimeoutError("event lock acquisition failed")
        yield  # pragma: no cover — the raise happens on enter


def _payload(call_id: str, name: str, args: dict, *, history: list[dict] | None = None) -> dict:
    messages: list[dict] = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": _json_args(args)},
                }
            ],
        },
    ]
    messages.extend(history or [])
    return {"messages": messages}


def _json_args(args: dict) -> str:
    import json

    return json.dumps(args)


def _execution_context(session_id: uuid.UUID) -> dict:
    return {"session_id": session_id, "superstep": 0, "trace_id": None}


def _execution_id(session_id: uuid.UUID, call_id: str) -> str:
    """The worker's deterministic execution id for (session, tool_call_id)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tool-exec:{session_id}:{call_id}"))


async def _inject_crash_window(
    store: InMemoryEventStore,
    session_id: uuid.UUID,
    call_id: str,
    name: str,
    args: dict,
) -> str:
    """Persist only the TOOL_CALL — the "write landed, receipt lost" window."""
    execution_id = _execution_id(session_id, call_id)
    await store.append(
        Event(
            session_id=session_id,
            superstep=0,
            event_type=EventType.TOOL_CALL,
            payload={
                "tool_name": name,
                "arguments": args,
                "tool_call_id": call_id,
                "execution_id": execution_id,
                "side_effect_class": "non_idempotent_write",
            },
        )
    )
    return execution_id


# ---------------------------------------------------------------------------
# Task 1.1 reproductions — red on the pre-fix code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_window_non_idempotent_write_not_reexecuted():
    """Plan G2 repro (non-idempotent variant): TOOL_CALL persisted, no
    TOOL_RESULT — a replay must withhold re-execution instead of running
    the write again."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    await _inject_crash_window(store, session_id, "call-1", "memory_add", {"content": "x"})

    result = await worker.execute("tools", {}, _payload("call-1", "memory_add", {"content": "x"}), ctx)

    assert len(port.calls) == 0, "claimed non-idempotent write must not re-execute"
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert "review" in msg["content"]


@pytest.mark.asyncio
async def test_store_unavailable_stops_idempotent_write():
    """An unreadable store must not degrade to "no receipt, go ahead":
    writes stop; only readonly tools proceed."""
    store = _BrokenReadStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "write_file", {"path": "f"}), ctx)
    assert len(port.calls) == 1

    store.fail_reads = True
    result = await worker.execute("tools", {}, _payload("call-1", "write_file", {"path": "f"}), ctx)

    assert len(port.calls) == 1, "store unavailability must stop writes"
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert "unavailable" in msg["content"]


@pytest.mark.asyncio
async def test_same_key_different_arguments_conflicts():
    """Same execution id with changed arguments is a conflict — the
    previous receipt must neither be reused as the result nor overwritten."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "a"}), ctx)
    assert len(port.calls) == 1

    result = await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "DIFFERENT"}), ctx)

    assert len(port.calls) == 1
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert "conflict" in msg["content"]


@pytest.mark.asyncio
async def test_concurrent_duplicate_worker_executes_once():
    """Two workers dispatching the same fresh non-idempotent call under a
    healthy store: the event-lock claim admits exactly one execution."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker_a = ToolWorker(port=port, event_store=store)
    worker_b = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)
    payload = _payload("call-1", "memory_add", {"content": "x"})

    await asyncio.gather(
        worker_a.execute("tools", {}, payload, ctx),
        worker_b.execute("tools", {}, payload, ctx),
    )

    assert len(port.calls) == 1, "duplicate workers must not double-execute a write"


# ---------------------------------------------------------------------------
# Four-state resolution (task 2.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_window_resolves_as_claimed():
    """A TOOL_CALL without TOOL_RESULT is `claimed` — distinct from both
    `never_started` and any terminal receipt state."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    execution_id = await _inject_crash_window(store, session_id, "call-1", "memory_add", {"content": "x"})

    resolution = await resolve_tool_execution_state(store, session_id, execution_id)

    assert resolution.state is ToolExecutionState.CLAIMED
    assert resolution.arguments_digest is None, "legacy claim without digest reads as unknown"

    other = await resolve_tool_execution_state(store, session_id, str(uuid.uuid4()))
    assert other.state is ToolExecutionState.NEVER_STARTED


@pytest.mark.asyncio
async def test_resolution_maps_receipt_statuses():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    ok_id = _execution_id(session_id, "call-1")
    ok = await resolve_tool_execution_state(store, session_id, ok_id)
    assert ok.state is ToolExecutionState.SUCCEEDED
    assert ok.arguments_digest is not None
    assert ok.result_digest is not None

    failing = _StubPort(raise_exc=ConnectionError("lost"))
    worker2 = ToolWorker(port=failing, event_store=store)
    await worker2.execute("tools", {}, _payload("call-2", "browser_click", {"x": "1"}), ctx)
    unknown = await resolve_tool_execution_state(store, session_id, _execution_id(session_id, "call-2"))
    assert unknown.state is ToolExecutionState.OUTCOME_UNKNOWN

    worker3 = ToolWorker(port=_StubPort(raise_exc=ValueError("bad args")), event_store=store)
    await worker3.execute("tools", {}, _payload("call-3", "web_search", {"query": "y"}), ctx)
    failed = await resolve_tool_execution_state(store, session_id, _execution_id(session_id, "call-3"))
    assert failed.state is ToolExecutionState.FAILED


@pytest.mark.asyncio
async def test_resolution_store_failure_is_explicit_state():
    store = _BrokenReadStore()
    store.fail_reads = True

    resolution = await resolve_tool_execution_state(store, uuid.uuid4(), str(uuid.uuid4()))

    assert resolution.state is ToolExecutionState.STORE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Honest success recovery (task 2.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_succeeded_recovers_real_result_from_channel_history():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    first = await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    assert len(port.calls) == 1
    first_msg = first.channel_updates["messages"][0]

    replay = await worker.execute(
        "tools",
        {},
        _payload("call-1", "web_search", {"query": "x"}, history=[first_msg]),
        ctx,
    )

    assert len(port.calls) == 1, "succeeded call must not re-execute"
    msg = replay.channel_updates["messages"][0]
    assert msg["content"] == first_msg["content"], "recovery must backfill the real result"
    assert "recovered" not in msg["content"].lower()


@pytest.mark.asyncio
async def test_succeeded_without_content_marks_reconciliation():
    """Receipt succeeded but the result content never reached the channel
    (checkpoint rolled back): explicit reconciliation marker with the
    recorded result digest — never fabricated content, never re-execution."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    assert len(port.calls) == 1

    replay = await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)

    assert len(port.calls) == 1
    msg = replay.channel_updates["messages"][0]
    assert "reconciliation" in msg["content"]
    assert "result_digest" in msg["content"]


# ---------------------------------------------------------------------------
# Store-level guards (tasks 2.4 / 3.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_unavailable_readonly_proceeds():
    store = _BrokenReadStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    store.fail_reads = True
    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)

    assert len(port.calls) == 2, "readonly tools proceed while the store is unavailable"


@pytest.mark.asyncio
async def test_lock_failure_does_not_execute_write():
    store = _LockFailureStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)

    result = await worker.execute(
        "tools",
        {},
        _payload("call-1", "memory_add", {"content": "x"}),
        _execution_context(session_id),
    )

    assert len(port.calls) == 0, "a lost claim race must not run a side-effecting tool"
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
