"""S02 — denied action: policy deny blocks the protected write with evidence.

Manifest: S02 (P06). A workspace deny rule on the ticket tool rejects the call
inside the real access-policy pipeline (before execution). The evidence is the
actual tool result fed back into the loop; the stub records zero invocations.
"""

from __future__ import annotations

from tests.scenarios.conftest import (
    SCENARIO_TOOL,
    agent_chat_url,
    chat_body,
    llm_response,
    tool_call,
)


async def test_s02_denied_action(
    scenario_client,
    scenario_agent,
    ticket_service,
    scripted_llm,
    add_policy_rule,
) -> None:
    await add_policy_rule(action="deny")

    seen_tool_results: list[str] = []

    async def responder(messages):
        if messages and messages[-1].get("role") == "tool":
            seen_tool_results.append(messages[-1]["content"])
            return llm_response(content="Acknowledged: the ticket write was denied.")
        return llm_response(
            tool_calls=[
                tool_call(
                    "call_s02_001",
                    SCENARIO_TOOL,
                    {"title": "Sneaky write", "body": "Must never reach the backend."},
                )
            ]
        )

    scripted_llm(responder)
    response = await scenario_client.post(
        agent_chat_url(scenario_agent),
        json=chat_body("File the ticket regardless of permissions."),
    )

    assert response.status_code == 200
    data = response.json()
    assert "denied" in data["choices"][0]["message"]["content"].lower()

    assert seen_tool_results == ["Error: Tool denied by access policy"], (
        "the loop must receive the real policy denial as the tool result"
    )
    assert ticket_service.side_effect_count == 0, "denied actions must never reach the backend"
