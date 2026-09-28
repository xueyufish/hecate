"""S05 — duplicate submit: resubmitting the same protected action is idempotent.

Manifest: S05 (P05). Gated on g2-tool-receipt-recovery (merged as #186).
Re-dispatching the identical (session, tool_call_id) action must not create a
second ticket: the recorded receipt short-circuits execution and the original
business result is returned consistently.
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

TICKET_ARGS = {"title": "Quarter close summary", "body": "Filed twice on purpose."}


async def test_s05_duplicate_submit_has_single_side_effect() -> None:
    store = InMemoryEventStore()
    ticket = StubTicketService(tool_name="create_test_ticket")
    worker = make_worker(store, ScenarioToolPort(ticket))
    session_id = uuid.uuid4()
    ctx = execution_context(session_id)

    first = await worker.execute("tools", {}, scenario_payload("call-s05-1", TICKET_ARGS), ctx)
    history = first.channel_updates["messages"]
    duplicate = await worker.execute("tools", {}, scenario_payload("call-s05-1", TICKET_ARGS, history=history), ctx)

    assert ticket.side_effect_count == 1, "duplicate submit must not repeat the protected write"

    first_msg = first.channel_updates["messages"][0]
    duplicate_msg = duplicate.channel_updates["messages"][0]
    assert "TKT-0000" in first_msg["content"]
    assert duplicate_msg["content"] == first_msg["content"], (
        "the duplicate submit must return the original business result, not a new one"
    )
