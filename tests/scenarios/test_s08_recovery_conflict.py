"""S08 — real-result recovery and same-key argument conflict.

Manifest: S08 (P05). Gated on g2-tool-receipt-recovery (merged as #186).

- recovery of a succeeded call restores the REAL recorded business result
  (the ticket id) from the channel history — never a placeholder;
- the same action key (session + tool_call_id) replayed with CHANGED
  arguments is rejected as a conflict and never executed.
"""

from __future__ import annotations

import uuid

from hecate.runtime.eventstore import InMemoryEventStore
from tests.scenarios.tools.receipt_harness import (
    ScenarioToolPort,
    execution_context,
    make_worker,
    scenario_payload,
)
from tests.scenarios.tools.stub_ticket import StubTicketService


async def test_s08_recovery_restores_real_result() -> None:
    store = InMemoryEventStore()
    ticket = StubTicketService(tool_name="create_test_ticket")
    session_id = uuid.uuid4()
    ctx = execution_context(session_id)
    worker = make_worker(store, ScenarioToolPort(ticket))

    first = await worker.execute("tools", {}, scenario_payload("call-s08-1", {"title": "T1", "body": "B1"}), ctx)
    first_msg = first.channel_updates["messages"][0]

    replay = await worker.execute(
        "tools",
        {},
        scenario_payload("call-s08-1", {"title": "T1", "body": "B1"}, history=[first_msg]),
        ctx,
    )
    replay_msg = replay.channel_updates["messages"][0]
    assert ticket.side_effect_count == 1, "recovery must not re-execute a succeeded call"
    assert "TKT-0000" in replay_msg["content"], "recovery must restore the real business result"
    assert replay_msg["content"] == first_msg["content"], "recovered result must equal the original"
    assert "reconciliation" not in replay_msg["content"].lower()


async def test_s08_same_key_changed_arguments_rejected() -> None:
    store = InMemoryEventStore()
    ticket = StubTicketService(tool_name="create_test_ticket")
    session_id = uuid.uuid4()
    ctx = execution_context(session_id)
    worker = make_worker(store, ScenarioToolPort(ticket))

    await worker.execute("tools", {}, scenario_payload("call-s08-2", {"title": "Original", "body": "A"}), ctx)
    assert ticket.side_effect_count == 1

    conflict = await worker.execute(
        "tools",
        {},
        scenario_payload("call-s08-2", {"title": "Tampered", "body": "B"}),
        ctx,
    )
    conflict_msg = conflict.channel_updates["messages"][0]
    assert ticket.side_effect_count == 1, "a conflicting same-key dispatch must not execute"
    assert "conflict" in conflict_msg["content"].lower(), "the withholding must be explicit about the argument conflict"
    assert conflict_msg.get("is_error") is True
