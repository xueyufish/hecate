"""S12 — feedback-driven multi-turn autonomous execution (step5e).

Deterministic (tier 1): a scripted responder reads the tool result content
and derives its follow-up write from it — proving the loop is feedback-
driven, not a fixed plan. Runs the SAME task through the direct loop (via
the chat endpoint) asserting: multi-turn tool selection, structured final
output when declared, unknown-outcome stop, and budget-exhausted stop.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.scenarios.baseline.run_autonomous_baseline import MATERIAL
from tests.scenarios.conftest import (
    agent_chat_url,
    chat_body,
    feedback_driven_responder,
    llm_response,
)

MATERIAL_QUERY = {"domain": "domain_a", "sku": "SKU-A1"}


def _feedback_responder():
    return feedback_driven_responder(
        read_tool="lookup_material",
        material_query=MATERIAL_QUERY,
        material_answer="stock confirmed",
        followup_tool="create_test_ticket",
        followup_args_from_material=lambda content: {
            "title": "Reorder after read",
            "body": json.loads(content)["ticket_body"],
        },
        final_content_from=lambda material, write: f"read={material}; wrote={write}",
        structured=True,
    )


async def _two_tool_agent(db_session, default_workspace):
    """Chat agent with a read tool + the scenario write tool."""
    from hecate.models.agent import AgentModel
    from hecate.models.tool import ToolModel
    from tests.scenarios.conftest import SCENARIO_TOOL

    read_tool = ToolModel(
        workspace_id=default_workspace.id,
        name="lookup_material",
        description="Read material facts for the task",
        source="custom",
        parameters={
            "type": "object",
            "properties": {"domain": {"type": "string"}, "sku": {"type": "string"}},
            "required": ["domain", "sku"],
        },
    )
    write_tool = ToolModel(
        workspace_id=default_workspace.id,
        name=SCENARIO_TOOL,
        description="Create a ticket",
        source="custom",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
            "required": ["title", "body"],
        },
    )
    agent = AgentModel(
        workspace_id=default_workspace.id,
        name="Autonomous Scenario Agent",
        model_config_db={"model": "gpt-4o"},
        mode="chat",
        tools=["lookup_material", SCENARIO_TOOL],
    )
    db_session.add_all([read_tool, write_tool, agent])
    await db_session.flush()
    return agent


@pytest.mark.parametrize("path", ["direct", "engine"])
@pytest.mark.parametrize("stream", [False, True])
async def test_s12_feedback_driven_multiturn_structured(
    client, scripted_llm, ticket_service, db_session, default_workspace, monkeypatch, path, stream
):
    """Read feedback, derive a write, and return validated structured content."""
    scenario_agent = await _two_tool_agent(db_session, default_workspace)
    responder, counters = _feedback_responder()
    scripted_llm(responder)

    # Deterministic DL path (same pin as the stop tests): local SQLite lacks
    # the feature-flag table; the flag matrix itself is G3's concern.

    async def direct_path(db, workspace_id, **kwargs):
        return path

    monkeypatch.setattr("hecate.execution.entry_routing.resolve_chat_engine_path", direct_path)

    from hecate.tools.tool.registry import ToolRegistry

    async def read_execute(self, name, args, context=None):
        if name == "lookup_material":
            return MATERIAL
        return None

    original_execute = ToolRegistry.execute

    async def patched(self, name, args, context=None):
        result = await read_execute(self, name, args, context)
        if result is not None:
            return result
        return await original_execute(self, name, args, context)

    monkeypatch.setattr(ToolRegistry, "execute", patched)

    body = chat_body("Check stock for SKU-A1, then create a reorder ticket from what you read.")
    body["stream"] = stream
    body["output_schema"] = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    response = await client.post(agent_chat_url(scenario_agent), json=body)
    assert response.status_code == 200, response.text
    if stream:
        chunks = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        # The entry contract emits a metadata-only correlation frame with an
        # empty ``choices`` list before content frames.  Only content frames
        # contribute to the reconstructed assistant response.
        content = "".join(
            chunk["choices"][0].get("delta", {}).get("content", "") or "" for chunk in chunks if chunk.get("choices")
        )
        assert response.text.endswith("data: [DONE]\n\n")
    else:
        content = response.json()["choices"][0]["message"]["content"]
    # The final answer is valid JSON per the declared schema and carries
    # BOTH round results (proves the loop consumed the tool feedback).
    document = json.loads(content)
    assert "stock" in document["answer"] and "confirmed" in document["answer"]
    assert "wrote=" in document["answer"]
    # The follow-up write really executed with derived arguments.
    assert ticket_service.side_effect_count == 1
    assert MATERIAL["ticket_body"] in str(ticket_service.calls)
    assert counters["reads"] == 1 and counters["writes"] == 1 and counters["finals"] == 1


@pytest.mark.parametrize("path", ["direct", "engine"])
@pytest.mark.parametrize("stream", [False, True])
async def test_s12_unknown_outcome_stops_loop(
    client, scripted_llm, db_session, default_workspace, monkeypatch, path, stream
):
    """Unknown outcomes stop both paths before any additional model round."""
    from hecate_governance.claims import OutcomeUnknownError

    responder, counters = _feedback_responder()
    scripted_llm(responder)

    # Deterministic DL path: force the direct tool loop (tests 2/3 exercise
    # direct-loop stop semantics; engine-path parity is covered by the
    # runtime recovery suites and the matrix).

    async def direct_path(db, workspace_id, **kwargs):
        return path

    monkeypatch.setattr("hecate.execution.entry_routing.resolve_chat_engine_path", direct_path)

    from hecate.tools.tool.registry import ToolRegistry

    original_execute = ToolRegistry.execute

    async def failing_execute(self, name, args, context=None):
        if name == "lookup_material":
            return MATERIAL
        if name == "create_test_ticket":
            raise OutcomeUnknownError("receipt write failed; outcome not established")
        return await original_execute(self, name, args, context)

    monkeypatch.setattr(ToolRegistry, "execute", failing_execute)
    scenario_agent = await _two_tool_agent(db_session, default_workspace)

    response = await client.post(
        agent_chat_url(scenario_agent),
        json={**chat_body("Check stock, then reorder."), "stream": stream},
    )
    # step5e: the loop surfaces the stop to the client as a 409 with the
    # machine-readable category — the model never sees the unknown outcome.
    if stream:
        assert response.status_code == 200
        errors = [
            json.loads(line[6:])["error"]
            for line in response.text.splitlines()
            if line.startswith("data: {") and '"error"' in line
        ]
        error = errors[0]
        assert response.text.endswith("data: [DONE]\n\n")
    else:
        assert response.status_code == 409, response.text
        error = response.json()["error"]
    assert error["reason_category"] == "needs_review"
    assert error["action_ref"]
    # The read round happened; the write attempt raised; no further rounds.
    assert counters["reads"] == 1
    assert counters["rounds"] == 2 and counters["finals"] == 0


@pytest.mark.parametrize("path", ["direct", "engine"])
@pytest.mark.parametrize("stream", [False, True])
async def test_s12_budget_exhausted_stops_loop(
    client, scripted_llm, db_session, default_workspace, monkeypatch, path, stream
):
    """Budget refusal is an explicit terminal condition on both paths."""
    responder, _ = _feedback_responder()
    scripted_llm(responder)

    # The loop's model round goes through GovernedModelService.chat — patch
    # the chat module's bound symbol so the BudgetRefusedError surfaces at
    # the loop's handler exactly as production raises it.
    from hecate.channel.api.v1 import chat as chat_mod
    from hecate.core.composition.governance import BudgetRefusedError

    async def direct_path(db, workspace_id, **kwargs):
        return path

    monkeypatch.setattr("hecate.execution.entry_routing.resolve_chat_engine_path", direct_path)

    class _RefusingLLM:
        async def chat(self, **kwargs):
            raise BudgetRefusedError("metered model call refused: budget state exhausted")

        async def chat_stream(self, **kwargs):
            raise BudgetRefusedError("metered model call refused: budget state exhausted")
            yield {}

    monkeypatch.setattr(chat_mod, "llm_service", _RefusingLLM())
    scenario_agent = await _two_tool_agent(db_session, default_workspace)

    response = await client.post(
        agent_chat_url(scenario_agent),
        json={**chat_body("Check stock, then reorder."), "stream": stream},
    )
    if stream:
        assert response.status_code == 200
        error = next(
            json.loads(line[6:])["error"]
            for line in response.text.splitlines()
            if line.startswith("data: {") and '"error"' in line
        )
        assert response.text.endswith("data: [DONE]\n\n")
    else:
        assert response.status_code == 409, response.text
        error = response.json()["error"]
    assert error["reason_category"] == "budget_exhausted"


async def test_s12_agent_request_rejects_malformed_schema(client, db_session, default_workspace):
    agent = await _two_tool_agent(db_session, default_workspace)
    response = await client.post(agent_chat_url(agent), json={**chat_body("hi"), "output_schema": {"type": "invalid"}})
    assert response.status_code == 422


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
async def test_s12_output_contract_cannot_fetch_remote_schema(client, db_session, default_workspace, keyword):
    agent = await _two_tool_agent(db_session, default_workspace)
    response = await client.post(
        agent_chat_url(agent), json={**chat_body("hi"), "output_schema": {keyword: "https://example.invalid/schema"}}
    )
    assert response.status_code == 422


def test_s12_local_schema_references_and_unresolvable_contract():
    from hecate.channel.api.v1.chat import _validate_output_schema
    from hecate.execution.workflow_child import WorkflowChildTaskAdapter

    local = {"$defs": {"answer": {"type": "string"}}, "$ref": "#/$defs/answer"}
    assert _validate_output_schema('"valid"', local) is None
    assert _validate_output_schema("42", local) is not None
    assert "cannot be evaluated" in _validate_output_schema("{}", {"$ref": "#/missing"})
    document = '{"$ref": "instance-data"}'
    assert _validate_output_schema(document, {"properties": {"$ref": {"type": "string"}}}) is None
    assert _validate_output_schema(document, {"const": {"$ref": "instance-data"}}) is None
    with pytest.raises(ValueError, match="local fragments"):
        WorkflowChildTaskAdapter.plan(
            {"workflow": {"steps": [{"goal": "work", "output_schema": {"$ref": "https://example.invalid/schema"}}]}}
        )


def _response_error(response, stream):
    if stream:
        assert response.status_code == 200
        assert response.text.endswith("data: [DONE]\n\n")
        return next(
            json.loads(line[6:])["error"]
            for line in response.text.splitlines()
            if line.startswith("data: {") and '"error"' in line
        )
    assert response.status_code == 409, response.text
    return response.json()["error"]


@pytest.mark.parametrize("path", ["direct", "engine"])
@pytest.mark.parametrize("stream", [False, True])
async def test_s12_invalid_output_repair_is_bounded_and_never_leaks(
    client, scripted_llm, db_session, default_workspace, monkeypatch, path, stream
):
    calls = []

    async def invalid(messages):
        calls.append(list(messages))
        return llm_response(content="INVALID_FINAL_SENTINEL")

    scripted_llm(invalid)

    async def route(*args, **kwargs):
        return path

    monkeypatch.setattr("hecate.execution.entry_routing.resolve_chat_engine_path", route)
    agent = await _two_tool_agent(db_session, default_workspace)
    response = await client.post(
        agent_chat_url(agent),
        json={**chat_body("Return the final result."), "stream": stream, "output_schema": {"type": "object"}},
    )
    assert _response_error(response, stream)["reason_category"] == "invalid_output"
    assert len(calls) == 2
    assert "INVALID_FINAL_SENTINEL" not in response.text
    assert calls[1][-1]["role"] == "user"


@pytest.mark.parametrize("path", ["direct", "engine"])
@pytest.mark.parametrize("stream", [False, True])
async def test_s12_cancellation_after_read_prevents_followup(
    client, scripted_llm, ticket_service, db_session, default_workspace, monkeypatch, path, stream
):
    from hecate.channel.api.v1 import agents as agents_mod
    from hecate.tools.tool.registry import ToolRegistry

    cancelled = False
    responder, counters = _feedback_responder()
    scripted_llm(responder)

    async def check():
        return cancelled

    monkeypatch.setattr(agents_mod, "_disconnect_check", lambda request: check)

    async def route(*args, **kwargs):
        return path

    monkeypatch.setattr("hecate.execution.entry_routing.resolve_chat_engine_path", route)
    original = ToolRegistry.execute

    async def read(self, name, args, context=None):
        nonlocal cancelled
        if name == "lookup_material":
            cancelled = True
            return MATERIAL
        return await original(self, name, args, context)

    monkeypatch.setattr(ToolRegistry, "execute", read)
    agent = await _two_tool_agent(db_session, default_workspace)
    response = await client.post(agent_chat_url(agent), json={**chat_body("Read then reorder."), "stream": stream})
    assert _response_error(response, stream)["reason_category"] == "cancelled"
    assert counters["rounds"] == 1
    assert ticket_service.side_effect_count == 0


@pytest.mark.parametrize("path", ["direct", "engine"])
@pytest.mark.parametrize("stream", [False, True])
async def test_s12_followup_write_uses_real_approval_chain(
    scenario_client,
    scripted_llm,
    ticket_service,
    db_session,
    default_workspace,
    monkeypatch,
    add_policy_rule,
    grant_approvals,
    scenario_event_store,
    path,
    stream,
):
    from hecate.runtime.eventstore import EventType
    from hecate.tools.tool.registry import ToolRegistry

    responder, counters = _feedback_responder()
    scripted_llm(responder)
    await add_policy_rule("ask")

    async def route(*args, **kwargs):
        return path

    monkeypatch.setattr("hecate.execution.entry_routing.resolve_chat_engine_path", route)
    original = ToolRegistry.execute

    async def read(self, name, args, context=None):
        if name == "lookup_material":
            return MATERIAL
        return await original(self, name, args, context)

    monkeypatch.setattr(ToolRegistry, "execute", read)
    agent = await _two_tool_agent(db_session, default_workspace)
    session_id = uuid.uuid4()
    response = await scenario_client.post(
        agent_chat_url(agent), json={**chat_body("Read then reorder.", session_id), "stream": stream}
    )
    assert response.status_code == 200, response.text
    assert '"error"' not in response.text
    assert ticket_service.side_effect_count == 1
    assert counters["rounds"] == 3
    events = await scenario_event_store.get_events(session_id)
    assert sum(event.event_type == EventType.APPROVAL_ASKED for event in events) == 1
    assert sum(event.event_type == EventType.APPROVAL_DECIDED for event in events) == 1
