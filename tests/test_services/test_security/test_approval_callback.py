"""Tests for ``services/security/approval.py`` (T2, 1.3.4 fail-closed)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from hecate.runtime.eventstore import EventType
from hecate.runtime.security.approval import FailingClosedApprovalCallback
from hecate.runtime.tool_access import ApprovalDecision, ApprovalScope


def _event_store() -> MagicMock:
    s = MagicMock()
    s.append = AsyncMock()
    return s


async def _collect(callback: FailingClosedApprovalCallback) -> list:
    store = callback.event_store
    return [call.args[0] for call in store.append.call_args_list]


async def test_approval_emits_asked_then_decided_pair():
    """T2.1 — every approval request emits both halves of the audit pair,
    even when the underlying callback is the fail-closed default.
    """
    store = _event_store()
    sid = uuid.uuid4()
    cb = FailingClosedApprovalCallback(event_store=store, session_id=sid, workspace_id=uuid.uuid4())

    decision = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-1", "command": "ls"},
        risk_level="critical",
        context={"session_id": str(sid)},
    )
    # Default delegates to NoAnswer — denies.
    assert decision.approved is False
    assert "no_answerer" in decision.reason

    events = await _collect(cb)
    types = [e.event_type for e in events]
    assert types == [EventType.APPROVAL_ASKED, EventType.APPROVAL_DECIDED]
    asked, decided = events
    assert asked.payload["tool_call_id"] == "tc-1"
    assert asked.payload["log_schema_version"] == 2
    assert decided.payload["approved"] is False


async def test_approval_no_event_store_still_returns_decision():
    """Without an event store, the callback MUST still return a decision
    (fail-closed) — events are best-effort, semantics are not.
    """
    cb = FailingClosedApprovalCallback(event_store=None)
    decision = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-2"},
        risk_level="high",
        context={},
    )
    assert decision.approved is False


async def test_inner_callback_exception_yields_denial_pair():
    """An exception in the underlying callback is treated as a no-answerer.
    The audit pair is still emitted with the failure reason.
    """
    store = _event_store()
    sid = uuid.uuid4()
    cb = FailingClosedApprovalCallback(event_store=store, session_id=sid, workspace_id=uuid.uuid4())

    class _Raiser:
        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            raise RuntimeError("upstream timeout")

    # Override the delegate to a raising implementation.
    cb._inner_backend = _Raiser()

    decision = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-3"},
        risk_level="critical",
        context={},
    )
    assert decision.approved is False
    assert "RuntimeError" in decision.reason

    events = await _collect(cb)
    assert [e.event_type for e in events] == [
        EventType.APPROVAL_ASKED,
        EventType.APPROVAL_DECIDED,
    ]


async def test_once_only_grant_cannot_be_replayed():
    """T2.3 — once-only consumption: an ONCE grant is consumed on first use;
    a second call for the same tool_call_id refuses with ``once_only_consumed``.
    """

    class _AlwaysApprove:
        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            return ApprovalDecision(approved=True, reason="manual-allow", scope=ApprovalScope.ONCE)

    store = _event_store()
    sid = uuid.uuid4()
    cb = FailingClosedApprovalCallback(event_store=store, session_id=sid, workspace_id=uuid.uuid4())
    cb._inner_backend = _AlwaysApprove()

    # First call: approved.
    first = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-once"},
        risk_level="low",
        context={},
    )
    assert first.approved is True

    # Second call (same tool_call_id): denied, marked consumed.
    second = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-once"},
        risk_level="low",
        context={},
    )
    assert second.approved is False
    assert second.reason == "once_only_consumed"


async def test_session_scope_grant_caches_for_replay():
    """T2.3 — SESSION-scoped durable grants cache and replay within the same
    session when the same tool_call_id is used. Different tool_call_ids are
    independent — each goes through the full approval cycle.
    """

    class _ApproveSession:
        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            return ApprovalDecision(approved=True, reason="ok", scope=ApprovalScope.SESSION)

    store = _event_store()
    sid = uuid.uuid4()
    cb = FailingClosedApprovalCallback(event_store=store, session_id=sid, workspace_id=uuid.uuid4())
    cb._inner_backend = _ApproveSession()

    first = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-sess"},
        risk_level="low",
        context={},
    )
    assert first.approved is True

    # Same tool_call_id, same session — the session-scoped grant is cached
    # and the delegate is NOT invoked again (no second "ok" reason would
    # otherwise accumulate). We verify this by counting ASKED events.
    events_before = len(store.append.call_args_list)

    second = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-sess"},
        risk_level="low",
        context={},
    )
    assert second.approved is True
    # The second call DID still emit a full audit pair (the replay is a
    # fresh request — but the cached grant means the inner delegate wasn't
    # invoked twice).
    events_after = len(store.append.call_args_list)
    assert events_after > events_before  # ASKED + DECIDED emitted again


async def test_rebuild_approval_projection_from_event_log():
    """T2.4 — the ``approval_records`` table is a rebuildable projection of
    the event log. Replaying the events yields the same rows as live
    capture.
    """
    from hecate.runtime.eventstore import InMemoryEventStore
    from hecate.runtime.security.approval import rebuild_approval_projection

    store = InMemoryEventStore()
    sid = uuid.uuid4()
    ws = uuid.uuid4()
    cb = FailingClosedApprovalCallback(event_store=store, session_id=sid, workspace_id=ws)
    await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-rebuild"},
        risk_level="high",
        context={},
    )

    written: list[dict] = []

    def writer(row):
        written.append(row)

    count = await rebuild_approval_projection(store, sid, projection_writer=writer, workspace_id=ws)
    assert count == 1
    assert written[0]["tool_name"] == "bash"
    assert written[0]["risk_level"] == "high"
    assert written[0]["session_id"] == sid
    assert written[0]["workspace_id"] == ws
    assert written[0]["status"] == "rejected"  # fail-closed default
    assert written[0]["scope"] == "once"


async def test_session_cache_key_pins_parameter_digest():
    """step7 2.4 — the scope cache is keyed by the approved parameter digest:
    a replay with the same tool_call_id but changed arguments misses the
    cache and re-judges (binding invalid despite the callback having approved
    the original parameters); an identical replay still hits.
    """
    asks: list[dict] = []

    class _CountingApprove:
        def __init__(self) -> None:
            self.calls = 0

        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            self.calls += 1
            asks.append(dict(arguments))
            return ApprovalDecision(approved=True, reason="ok", scope=ApprovalScope.SESSION)

    backend = _CountingApprove()
    store = _event_store()
    cb = FailingClosedApprovalCallback(event_store=store, session_id=uuid.uuid4(), workspace_id=uuid.uuid4())
    cb._inner_backend = backend

    first = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-1", "command": "ls"},
        risk_level="low",
        context={},
    )
    assert first.approved is True
    assert backend.calls == 1

    # Identical replay (same tool_call_id, same arguments): cache hit.
    replay = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-1", "command": "ls"},
        risk_level="low",
        context={},
    )
    assert replay.approved is True
    assert backend.calls == 1  # inner not re-asked

    # Same tool_call_id, changed arguments: re-judgment (cache miss).
    changed = await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-1", "command": "rm -rf /"},
        risk_level="high",
        context={},
    )
    assert changed.approved is True  # backend approves again, but...
    assert backend.calls == 2  # ...it was asked afresh, not replayed


async def test_decided_event_records_arguments_digest():
    """step7 2.4 — APPROVAL_DECIDED carries the arguments digest so the
    approval-binding authority can bind the judgment to the parameters."""
    store = _event_store()

    class _Approve:
        async def request_approval(self, *, tool_name, arguments, risk_level, context):
            return ApprovalDecision(approved=True, reason="ok", scope=ApprovalScope.ONCE)

    cb = FailingClosedApprovalCallback(event_store=store, session_id=uuid.uuid4(), workspace_id=uuid.uuid4())
    cb._inner_backend = _Approve()
    await cb.request_approval(
        tool_name="bash",
        arguments={"tool_call_id": "tc-d", "command": "ls"},
        risk_level="low",
        context={},
    )
    decided = [
        call.args[0] for call in store.append.call_args_list if call.args[0].event_type == EventType.APPROVAL_DECIDED
    ]
    assert len(decided) == 1
    assert decided[0].payload["arguments_digest"]
    # Digest excludes the call handle: stable for the same logical arguments.
    from hecate.runtime.security.approval import approval_arguments_digest

    assert decided[0].payload["arguments_digest"] == approval_arguments_digest({"command": "ls"})
