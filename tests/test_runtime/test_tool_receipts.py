"""Receipt semantics for tool executions (tool-recovery).

Pins the contract that every dispatched TOOL_CALL gets a paired
TOOL_RESULT receipt — succeeded, failed (definitively did not take
effect), or unknown (indeterminate) — keyed by a server-assigned stable
``execution_id``, plus the retry-decision matrix over side-effect
classes, and the Temporal fail-fast.
"""

from __future__ import annotations

import json
import uuid

import pytest

from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.temporal.run_worker import main as run_worker_main
from hecate.runtime.temporal.worker_pool import TemporalWorkerPool
from hecate.runtime.tool_side_effects import (
    RECEIPT_FAILED,
    RECEIPT_SUCCEEDED,
    RECEIPT_UNKNOWN,
    SideEffectClass,
    classify,
    should_auto_retry,
)
from hecate.runtime.workers.tool_worker import ToolWorker, get_tool_receipt
from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS


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

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


def _payload(call_id: str, name: str, args: dict) -> dict:
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
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            },
        ]
    }


def _make_worker(event_store: InMemoryEventStore, port: _StubPort) -> ToolWorker:
    return ToolWorker(port=port, event_store=event_store)


def _execution_context(session_id: uuid.UUID) -> dict:
    return {"session_id": session_id, "superstep": 0, "trace_id": None}


async def _tool_results(store: InMemoryEventStore, session_id: uuid.UUID) -> list:
    events = await store.get_events(session_id)
    return [e for e in events if e.event_type is EventType.TOOL_RESULT]


async def _tool_calls(store: InMemoryEventStore, session_id: uuid.UUID) -> list:
    events = await store.get_events(session_id)
    return [e for e in events if e.event_type is EventType.TOOL_CALL]


@pytest.mark.asyncio
async def test_successful_execution_has_paired_receipt():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    worker = _make_worker(store, _StubPort())

    await worker.execute(
        "tools",
        {},
        _payload("call-1", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )

    calls = await _tool_calls(store, session_id)
    receipts = await _tool_results(store, session_id)
    assert len(calls) == 1 and len(receipts) == 1
    assert receipts[0].payload["status"] == RECEIPT_SUCCEEDED
    assert receipts[0].payload["execution_id"] == calls[0].payload["execution_id"]
    assert receipts[0].payload["side_effect_class"] == SideEffectClass.READONLY.value


@pytest.mark.asyncio
async def test_connection_error_records_unknown_receipt():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    worker = _make_worker(store, _StubPort(raise_exc=ConnectionError("lost")))

    await worker.execute(
        "tools",
        {},
        _payload("call-1", "write_file", {"path": "f", "content": "x"}),
        _execution_context(session_id),
    )

    receipts = await _tool_results(store, session_id)
    assert len(receipts) == 1
    assert receipts[0].payload["status"] == RECEIPT_UNKNOWN


@pytest.mark.asyncio
async def test_definitive_error_records_failed_receipt():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    worker = _make_worker(store, _StubPort(raise_exc=ValueError("bad arguments")))

    await worker.execute(
        "tools",
        {},
        _payload("call-1", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )

    receipts = await _tool_results(store, session_id)
    assert len(receipts) == 1
    assert receipts[0].payload["status"] == RECEIPT_FAILED


@pytest.mark.asyncio
async def test_empty_llm_call_id_still_gets_execution_id():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    worker = _make_worker(store, _StubPort())

    await worker.execute(
        "tools",
        {},
        _payload("", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )

    calls = await _tool_calls(store, session_id)
    assert calls[0].payload["tool_call_id"] == ""
    assert calls[0].payload["execution_id"]
    receipts = await _tool_results(store, session_id)
    assert receipts[0].payload["execution_id"] == calls[0].payload["execution_id"]


@pytest.mark.asyncio
async def test_receipt_query_returns_latest_by_execution_id():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    worker = _make_worker(store, _StubPort())

    await worker.execute(
        "tools",
        {},
        _payload("call-1", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )
    execution_id = (await _tool_calls(store, session_id))[0].payload["execution_id"]

    receipt = await get_tool_receipt(store, session_id, execution_id)
    assert receipt is not None
    assert receipt["status"] == RECEIPT_SUCCEEDED
    assert await get_tool_receipt(store, session_id, "missing-execution-id") is None


def test_retry_decision_matrix():
    # Readonly: retryable when failed or no receipt; never when succeeded/unknown.
    assert should_auto_retry(SideEffectClass.READONLY, RECEIPT_FAILED) is True
    assert should_auto_retry(SideEffectClass.READONLY, None) is True
    assert should_auto_retry(SideEffectClass.READONLY, RECEIPT_SUCCEEDED) is False
    assert should_auto_retry(SideEffectClass.READONLY, RECEIPT_UNKNOWN) is False
    # Idempotent write: same as readonly.
    assert should_auto_retry(SideEffectClass.IDEMPOTENT_WRITE, RECEIPT_FAILED) is True
    assert should_auto_retry(SideEffectClass.IDEMPOTENT_WRITE, RECEIPT_UNKNOWN) is False
    # Non-idempotent / external: only on definitive failure.
    assert should_auto_retry(SideEffectClass.NON_IDEMPOTENT_WRITE, RECEIPT_FAILED) is True
    assert should_auto_retry(SideEffectClass.NON_IDEMPOTENT_WRITE, RECEIPT_UNKNOWN) is False
    assert should_auto_retry(SideEffectClass.EXTERNAL_SIDE_EFFECT, None) is False
    # Unknown classification: never.
    assert should_auto_retry(SideEffectClass.UNKNOWN, RECEIPT_FAILED) is False
    assert should_auto_retry(SideEffectClass.UNKNOWN, None) is False


def test_unregistered_tools_default_to_unknown():
    assert classify("some_custom_tool") is SideEffectClass.UNKNOWN


def test_builtin_tools_all_registered():
    """Every built-in tool name carries an explicit classification — new
    built-ins must be added to the registry (or deliberately listed as
    unknown) so recovery semantics stay explicit."""
    mapped = _mapped_names()
    unregistered = {name for name, cls in mapped.items() if cls is None}
    assert unregistered == set()


def _mapped_names() -> dict[str, SideEffectClass | None]:
    from hecate.runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS

    deliberate_unknown: set[str] = set()
    return {
        name: _BUILTIN_CLASSIFICATIONS.get(name) if name not in deliberate_unknown else SideEffectClass.UNKNOWN
        for name in BUILTIN_TOOL_DEFINITIONS
    }


@pytest.mark.asyncio
async def test_temporal_worker_pool_fails_fast():
    pool = TemporalWorkerPool()

    with pytest.raises(NotImplementedError, match="not implemented"):
        await pool.dispatch(
            worker=None,  # type: ignore[arg-type]
            node_id="n1",
            node_config={},
            channel_snapshot={},
        )


@pytest.mark.asyncio
async def test_temporal_run_worker_rejects_empty_activities():
    with pytest.raises(RuntimeError, match="no registered Activities"):
        await run_worker_main()


# ---------------------------------------------------------------------------
# Resume replay consumption (chat-loop-engine-convergence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_does_not_reexecute_succeeded_call():
    """A re-dispatch of the same (session, tool_call_id) after a succeeded
    receipt must not re-run the tool — the result is restored from the
    receipt (deterministic execution_id makes the replay match)."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = _make_worker(store, port)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    assert len(port.calls) == 1

    replay = await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    assert len(port.calls) == 1, "succeeded tool must not re-execute on resume"
    replay_msg = replay.channel_updates["messages"][0]
    assert "[recovered]" in replay_msg["content"]


@pytest.mark.asyncio
async def test_resume_unknown_outcome_goes_to_human_review():
    """After an indeterminate failure (connection lost), a resume dispatch
    must withhold automatic retry for an external-side-effect tool."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort(raise_exc=ConnectionError("lost"))
    worker = _make_worker(store, port)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "browser_click", {"x": "1"}), ctx)
    assert len(port.calls) == 1

    port.raise_exc = None
    replay = await worker.execute("tools", {}, _payload("call-1", "browser_click", {"x": "1"}), ctx)
    assert len(port.calls) == 1, "unknown outcome must not auto-retry"
    replay_msg = replay.channel_updates["messages"][0]
    assert "[needs review]" in replay_msg["content"]
    assert replay_msg["is_error"] is True


@pytest.mark.asyncio
async def test_resume_failed_readonly_call_retries():
    """A definitive failure of a readonly tool is retryable on resume —
    the call re-executes under the same execution_id."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort(raise_exc=ValueError("bad args"))
    worker = _make_worker(store, port)
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    assert len(port.calls) == 1

    port.raise_exc = None
    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    assert len(port.calls) == 2, "failed readonly call is retryable on resume"


@pytest.mark.asyncio
async def test_deterministic_execution_id_across_replays():
    """Same (session, tool_call_id) → same execution_id on every dispatch;
    different sessions never collide."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    worker = _make_worker(store, _StubPort())
    ctx = _execution_context(session_id)

    await worker.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    first_id = (await _tool_calls(store, session_id))[0].payload["execution_id"]

    store2 = InMemoryEventStore()
    worker2 = _make_worker(store2, _StubPort())
    await worker2.execute("tools", {}, _payload("call-1", "web_search", {"query": "x"}), ctx)
    replay_id = (await _tool_calls(store2, session_id))[0].payload["execution_id"]

    assert first_id == replay_id
