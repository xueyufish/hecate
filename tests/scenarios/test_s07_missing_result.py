"""S07 — missing result: recovery stops instead of repeating the write.

Manifest: S07 (P05). Gated on g2-tool-receipt-recovery (merged as #186).
Two crash windows leave the recorded call without a usable outcome:

- claimed: the process died after the claim (TOOL_CALL) but before any
  receipt — recovery must withhold the side-effecting call for review;
- outcome_unknown: the backend performed the side effect then the transport
  died (indeterminate error) — recovery must not auto-retry.

In both windows the side effect happens exactly once.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from hecate.runtime.eventstore import InMemoryEventStore
from hecate.runtime.workers.tool_worker import ToolExecutionState, resolve_tool_execution_state
from tests.scenarios.tools.receipt_harness import (
    ScenarioToolPort,
    execution_context,
    make_worker,
    scenario_payload,
)
from tests.scenarios.tools.stub_ticket import StubTicketService

TICKET_ARGS = {"title": "Crash-window filing", "body": "Result lost before persistence."}


async def test_s07_claimed_state_withholds_side_effecting_call() -> None:
    store = InMemoryEventStore()
    ticket = StubTicketService(tool_name="create_test_ticket")
    session_id = uuid.uuid4()
    ctx = execution_context(session_id)

    crashing = ScenarioToolPort(ticket, raise_exc=asyncio.CancelledError())
    worker = make_worker(store, crashing)
    # The simulated crash: the claim persisted, the receipt never will be.
    with contextlib.suppress(asyncio.CancelledError):
        await worker.execute("tools", {}, scenario_payload("call-s07-1", TICKET_ARGS), ctx)
    assert ticket.side_effect_count == 1, "the side effect itself happened before the crash"

    resolution = await resolve_tool_execution_state(store, session_id, await _execution_id(store, session_id))
    assert resolution.state is ToolExecutionState.CLAIMED, "crash after claim must resolve to the claimed state"

    recovered = ScenarioToolPort(ticket)
    replay = await make_worker(store, recovered).execute("tools", {}, scenario_payload("call-s07-1", TICKET_ARGS), ctx)
    assert ticket.side_effect_count == 1, "a claimed side-effecting call must not re-execute on recovery"
    assert "review" in replay.channel_updates["messages"][0]["content"].lower()


async def test_s07_unknown_outcome_goes_to_review_not_retry() -> None:
    store = InMemoryEventStore()
    ticket = StubTicketService(tool_name="create_test_ticket")
    session_id = uuid.uuid4()
    ctx = execution_context(session_id)

    indeterminate = ScenarioToolPort(ticket, raise_exc=ConnectionError("transport lost after write"))
    worker = make_worker(store, indeterminate)
    await worker.execute("tools", {}, scenario_payload("call-s07-2", TICKET_ARGS), ctx)
    assert ticket.side_effect_count == 1

    resolution = await resolve_tool_execution_state(store, session_id, await _execution_id(store, session_id))
    assert resolution.state is ToolExecutionState.OUTCOME_UNKNOWN

    recovered = ScenarioToolPort(ticket)
    replay = await make_worker(store, recovered).execute("tools", {}, scenario_payload("call-s07-2", TICKET_ARGS), ctx)
    assert ticket.side_effect_count == 1, "unknown outcome must never blind-retry a protected write"
    assert "review" in replay.channel_updates["messages"][0]["content"].lower()


async def _execution_id(store: InMemoryEventStore, session_id: uuid.UUID) -> str:
    """Read the claim's execution_id back from the event store."""
    events = await store.get_events(session_id)
    claims = [e for e in events if e.event_type.value == "TOOL_CALL"]
    assert claims, "dispatch must persist a TOOL_CALL claim"
    return claims[0].payload["execution_id"]
