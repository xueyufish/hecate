"""ActionLedgerHook integration in ToolWorker (step6 durable-execution-core).

Pins the kernel half of the G2 closure: with a persistent action-ledger hook
configured, intent/claim/outcome are mirrored with the correlation identity
that links a platform Action to the runtime's TOOL_CALL/TOOL_RESULT events;
the ledger is a recovery authority that gates dispatch even when the event
store is empty (post-restart); succeeded actions backfill the ledger's real
recorded content; outcome-write failures keep the run's result while the
action stays claimed (pending reconciliation); and with no hook configured
the worker behaves exactly as before.
"""

from __future__ import annotations

import json
import uuid

import pytest

from hecate.runtime.action_ledger import (
    ActionClaimVerdict,
    ActionLedgerHook,
    ToolExecutionResolution,
    ToolExecutionState,
    stable_execution_id,
)
from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.workers.tool_worker import ToolWorker


class _StubPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.trace: list[tuple[str, str]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        self.trace.append(("dispatch", name))
        return {"executed": True}

    async def tool_execute_sandbox(self, name, args, context=None):
        return await self.tool_execute(name, args, context)

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


class RecordingLedgerHook(ActionLedgerHook):
    """In-memory ledger double recording the mirror sequence."""

    def __init__(self) -> None:
        self.claims: list[dict] = []
        self.outcomes: list[dict] = []
        self.resolve_overrides: dict[str, ToolExecutionResolution] = {}
        self.claim_verdicts: dict[str, ActionClaimVerdict] = {}
        self.resolve_raises = False
        self.outcome_raises = False

    async def resolve(self, *, session_id: str, execution_id: str) -> ToolExecutionResolution:
        if self.resolve_raises:
            raise RuntimeError("ledger unreachable")
        return self.resolve_overrides.get(execution_id, ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED))

    async def record_claim(self, **kwargs) -> ActionClaimVerdict:
        self.claims.append(kwargs)
        verdict = self.claim_verdicts.get(kwargs["execution_id"])
        if verdict is not None:
            return verdict
        return ActionClaimVerdict(claimed=True)

    async def record_outcome(self, **kwargs) -> None:
        if self.outcome_raises:
            raise RuntimeError("ledger outcome write failed")
        self.outcomes.append(kwargs)


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
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        },
    ]
    messages.extend(history or [])
    return {"messages": messages}


def _execution_context(session_id) -> dict:
    return {"session_id": session_id, "superstep": 0, "trace_id": None}


def _last_result(result) -> dict:
    messages = result.channel_updates["messages"]
    assert messages, "expected a tool result message"
    return messages[-1]


@pytest.mark.asyncio
async def test_hook_mirrors_claim_and_outcome_with_event_correlation():
    """Claim precedes dispatch; outcome carries the raw result; the hook's
    correlation ids equal the TOOL_CALL/TOOL_RESULT execution ids."""

    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    hook = RecordingLedgerHook()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store, action_hook=hook)

    channel = await worker.execute(
        "tools",
        {},
        _payload("call-1", "write_file", {"path": "f", "content": "x"}),
        _execution_context(session_id),
    )

    assert port.calls == [("write_file", {"path": "f", "content": "x"})]
    execution_id = stable_execution_id(session_id, "call-1")
    assert len(hook.claims) == 1
    claim = hook.claims[0]
    assert claim["execution_id"] == execution_id
    assert claim["tool_call_id"] == "call-1"
    assert claim["tool_name"] == "write_file"
    assert claim["side_effect_class"] == "idempotent_write"
    assert len(hook.outcomes) == 1
    assert hook.outcomes[0]["status"] == "succeeded"
    assert hook.outcomes[0]["result"] == {"executed": True}
    # The mirrored Action and the runtime events share the correlation id.
    events = await store.get_events(session_id)
    for event in events:
        assert event.payload["execution_id"] == execution_id
        assert event.payload["tool_call_id"] == "call-1"
    assert not _last_result(channel).get("is_error")


@pytest.mark.asyncio
async def test_ledger_claimed_write_withholds_without_event_store():
    """Post-restart shape: the event store is gone but the ledger remembers
    a claimed write — dispatch is withheld, never re-run."""

    hook = RecordingLedgerHook()
    session_id = uuid.uuid4()
    call_id = "call-1"
    execution_id = stable_execution_id(session_id, call_id)
    hook.claim_verdicts[execution_id] = ActionClaimVerdict(
        claimed=False,
        resolution=ToolExecutionResolution(
            state=ToolExecutionState.CLAIMED,
            arguments_digest=hook_digest_for({"path": "f", "content": "x"}),
            tool_name="write_file",
        ),
    )
    port = _StubPort()
    worker = ToolWorker(port=port, action_hook=hook)  # no event store at all

    channel = await worker.execute(
        "tools",
        {},
        _payload(call_id, "write_file", {"path": "f", "content": "x"}),
        _execution_context(session_id),
    )

    assert port.calls == [], "a claimed write must not re-execute"
    message = _last_result(channel)
    assert message["is_error"] is True
    assert "needs review" in message["content"]


@pytest.mark.asyncio
async def test_ledger_succeeded_backfills_real_content_without_event_store():
    """A succeeded action recovers its REAL recorded result from the ledger
    when channel history is gone — no re-execution, no placeholder."""

    hook = RecordingLedgerHook()
    session_id = uuid.uuid4()
    call_id = "call-9"
    execution_id = stable_execution_id(session_id, call_id)
    hook.resolve_overrides[execution_id] = ToolExecutionResolution(
        state=ToolExecutionState.SUCCEEDED,
        arguments_digest=hook_digest_for({"path": "f", "content": "x"}),
        tool_name="write_file",
        result_digest="deadbeef",
        result_content='{"executed": true, "run": "before-crash"}',
    )
    port = _StubPort()
    worker = ToolWorker(port=port, action_hook=hook)

    channel = await worker.execute(
        "tools",
        {},
        _payload(call_id, "write_file", {"path": "f", "content": "x"}),
        _execution_context(session_id),
    )

    assert port.calls == [], "a succeeded action must not re-execute"
    message = _last_result(channel)
    assert message["content"] == '{"executed": true, "run": "before-crash"}'
    assert not message.get("is_error")


@pytest.mark.asyncio
async def test_ledger_digest_conflict_withholds():
    """Same action key, different arguments — explicit conflict, no dispatch."""

    hook = RecordingLedgerHook()
    session_id = uuid.uuid4()
    call_id = "call-2"
    execution_id = stable_execution_id(session_id, call_id)
    hook.resolve_overrides[execution_id] = ToolExecutionResolution(
        state=ToolExecutionState.CLAIMED,
        arguments_digest="0" * 64,
        tool_name="memory_add",
    )
    port = _StubPort()
    worker = ToolWorker(port=port, action_hook=hook)

    channel = await worker.execute(
        "tools",
        {},
        _payload(call_id, "memory_add", {"content": "different"}),
        _execution_context(session_id),
    )

    assert port.calls == []
    message = _last_result(channel)
    assert "[conflict] arguments differ" in message["content"]


@pytest.mark.asyncio
async def test_ledger_claim_verdict_conflict_withholds():
    hook = RecordingLedgerHook()
    session_id = uuid.uuid4()
    call_id = "call-3"
    execution_id = stable_execution_id(session_id, call_id)
    hook.claim_verdicts[execution_id] = ActionClaimVerdict(
        claimed=False, conflict="[conflict] recorded execution binds different parameters"
    )
    port = _StubPort()
    worker = ToolWorker(port=port, action_hook=hook)

    channel = await worker.execute(
        "tools",
        {},
        _payload(call_id, "memory_add", {"content": "x"}),
        _execution_context(session_id),
    )

    assert port.calls == []
    assert "[conflict]" in _last_result(channel)["content"]


@pytest.mark.asyncio
async def test_ledger_unreachable_fails_closed_for_writes_and_open_for_readonly():
    hook = RecordingLedgerHook()
    hook.resolve_raises = True
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, action_hook=hook)

    write_channel = await worker.execute(
        "tools",
        {},
        _payload("call-w", "memory_add", {"content": "x"}),
        _execution_context(session_id),
    )
    assert port.calls == []
    write_message = _last_result(write_channel)
    assert write_message["is_error"] is True
    assert "withheld" in write_message["content"]

    read_channel = await worker.execute(
        "tools",
        {},
        _payload("call-r", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )
    assert port.calls == [("web_search", {"query": "x"})]
    assert not _last_result(read_channel).get("is_error")


@pytest.mark.asyncio
async def test_outcome_mirror_failure_keeps_result_and_leaves_claim():
    """The side effect already happened; a failed outcome mirror must not
    fail the run — the ledger stays claimed, so recovery fails closed."""

    hook = RecordingLedgerHook()
    hook.outcome_raises = True
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=InMemoryEventStore(), action_hook=hook)

    channel = await worker.execute(
        "tools",
        {},
        _payload("call-4", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )

    assert port.calls == [("web_search", {"query": "x"})]
    message = _last_result(channel)
    assert not message.get("is_error")
    assert hook.claims and not hook.outcomes, "claim mirrored, outcome not recorded"


@pytest.mark.asyncio
async def test_no_hook_behavior_unchanged():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort()
    worker = ToolWorker(port=port, event_store=store)

    channel = await worker.execute(
        "tools",
        {},
        _payload("call-5", "web_search", {"query": "x"}),
        _execution_context(session_id),
    )

    assert port.calls == [("web_search", {"query": "x"})]
    events = await store.get_events(session_id)
    assert [e.event_type for e in events] == [EventType.TOOL_CALL, EventType.TOOL_RESULT]
    assert not _last_result(channel).get("is_error")


def hook_digest_for(arguments: dict) -> str:
    from hecate.runtime.action_ledger import tool_arguments_digest

    return tool_arguments_digest(arguments)
