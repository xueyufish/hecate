"""S03 — approval wait: ask rules gate the protected write on an approval.

Manifest: S03 (P06, P07). Two facets of the real approval path:

- fail-closed (no answerer): the callback persists the APPROVAL_ASKED /
  APPROVAL_DECIDED pair and the action never executes;
- approved: with a scripted human answerer on the documented
  ``_inner_backend`` seam, the action executes exactly once and the approved
  decision is durably recorded.
"""

from __future__ import annotations

import uuid

from hecate.runtime.eventstore import EventType
from tests.scenarios.conftest import (
    SCENARIO_TOOL,
    agent_chat_url,
    chat_body,
    llm_response,
    tool_call,
)

TICKET_ARGS = {"title": "Escalated expense fix", "body": "Requires human approval."}


def _responder(capture: list[str], final_content: str):
    async def responder(messages):
        if messages and messages[-1].get("role") == "tool":
            capture.append(messages[-1]["content"])
            return llm_response(content=final_content)
        return llm_response(tool_calls=[tool_call("call_s03_001", SCENARIO_TOOL, TICKET_ARGS)])

    return responder


async def test_s03_approval_fail_closed_persists_audit_pair(
    scenario_client,
    scenario_agent,
    ticket_service,
    scripted_llm,
    add_policy_rule,
    scenario_event_store,
) -> None:
    await add_policy_rule(action="ask")
    session_id = uuid.uuid4()

    capture: list[str] = []
    scripted_llm(_responder(capture, "Acknowledged."))
    response = await scenario_client.post(
        agent_chat_url(scenario_agent),
        json=chat_body("File the escalated ticket.", session_id=session_id),
    )

    assert response.status_code == 200
    assert ticket_service.side_effect_count == 0, "unapproved actions must not execute"
    assert len(capture) == 1 and "rejected" in capture[0].lower(), (
        "the loop must receive the fail-closed rejection as the tool result"
    )

    events = await scenario_event_store.get_events(session_id=session_id, from_version=0)
    pair = [
        (e.event_type, e.payload)
        for e in events
        if e.event_type in (EventType.APPROVAL_ASKED, EventType.APPROVAL_DECIDED)
    ]
    assert [t for t, _ in pair] == [EventType.APPROVAL_ASKED, EventType.APPROVAL_DECIDED], (
        "the durable audit pair must be emitted in order"
    )
    asked_payload, decided_payload = pair[0][1], pair[1][1]
    assert asked_payload["tool_name"] == SCENARIO_TOOL
    assert decided_payload["approved"] is False


async def test_s03_approval_granted_executes_action_once(
    scenario_client,
    scenario_agent,
    ticket_service,
    scripted_llm,
    add_policy_rule,
    grant_approvals,
    scenario_event_store,
) -> None:
    await add_policy_rule(action="ask")
    session_id = uuid.uuid4()

    capture: list[str] = []
    scripted_llm(_responder(capture, "Approved and filed as ticket TKT-0000."))
    response = await scenario_client.post(
        agent_chat_url(scenario_agent),
        json=chat_body("File the escalated ticket.", session_id=session_id),
    )

    assert response.status_code == 200
    assert "TKT-0000" in response.json()["choices"][0]["message"]["content"]
    assert ticket_service.side_effect_count == 1, "an approved action executes exactly once"

    events = await scenario_event_store.get_events(session_id=session_id, from_version=0)
    decided = [e for e in events if e.event_type == EventType.APPROVAL_DECIDED]
    assert len(decided) == 1
    assert decided[0].payload["approved"] is True
