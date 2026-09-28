"""S01 — normal execution: agent tool loop completes a protected write.

Manifest: S01 (P05). The agent proposes a ticket write through the real chat
entry, the stub ticket service executes exactly once, and the final answer
references the business result. Assertions compare business outcomes (ticket
id, side-effect count, recorded arguments) — never verbatim model text.
"""

from __future__ import annotations

from tests.scenarios.conftest import (
    agent_chat_url,
    chat_body,
    ticket_loop_responder,
)


async def test_s01_normal_execution(
    scenario_client,
    scenario_agent,
    ticket_service,
    scripted_llm,
) -> None:
    scripted_llm(
        ticket_loop_responder(
            ticket_args={
                "title": "Expense procedure summary",
                "body": "Standard limit 800 credits; 801-5000 budget owner; 5001+ finance committee.",
            },
            final_content="Filed the summary as ticket TKT-0000.",
        )
    )
    response = await scenario_client.post(
        agent_chat_url(scenario_agent),
        json=chat_body("Summarize the reimbursement procedure and file it as a ticket."),
    )

    assert response.status_code == 200
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    assert "TKT-0000" in content, "final answer must reference the real business result"

    assert ticket_service.side_effect_count == 1, "protected write must execute exactly once"
    call = ticket_service.calls[0]
    assert call.arguments["title"] == "Expense procedure summary"
    assert call.outcome == "created"
    assert call.context.get("workspace_id") == str(scenario_agent.workspace_id)
