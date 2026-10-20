"""Turn feedback projection and responder continuation (step6).

Covers the turn→messages projection and the explicit responder
continuation entry: a fully succeeded ``AgentTurn`` projects into the
assistant ``tool_calls`` message plus ordered governed tool results;
final answers, non-succeeded actions, partial dispatches, mismatched
identities and malformed receipts are explicitly rejected; inputs are
never mutated. Two chained ``AgentTurn`` runs prove the second model
call actually receives the first turn's projected tool results.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

import pytest
from hecate_runtime.accepted_action_batch import AcceptedActionBatchReport
from hecate_runtime.accepted_action_step import (
    AcceptedActionOutcome,
    execution_id_for_action,
    tool_call_id_for_action,
)
from hecate_runtime.accepted_decision import (
    ACTION_DECISION_SCHEMA_VERSION,
    FINAL_ANSWER_SCHEMA_VERSION,
    AcceptedAction,
    AcceptedDecisionRecord,
    DecisionValidationError,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.agent_turn import AgentTurn, AgentTurnOutcome
from hecate_runtime.eventstore import InMemoryEventStore
from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostToolHook
from hecate_runtime.port_decision_responder import PortDecisionResponder
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor
from hecate_runtime.turn_projection import project_turn_messages
from test_accepted_action_step import STUB_OWNER, StubLedger, StubToolPort, _run_ref
from test_decision_step import InMemoryDecisionStore

TOOL_SNAPSHOT: dict[str, Any] = {
    name: {"type": "function", "function": {"name": name, "description": "Test tool", "parameters": {"type": "object"}}}
    for name in ("biz_tool", "web_search")
}
REFS = {"config": "v1"}


class StubStructuredPort(StubRuntimePort):
    """Structured model port recording every received message list."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        super().__init__()
        self.calls = 0
        self.received: list[list[dict[str, Any]]] = []
        self._chunks = chunks

    async def llm_invoke_structured(self, messages: list[dict], config: dict) -> AsyncGenerator[dict[str, Any], None]:
        for message in messages:
            for call in message.get("tool_calls", []):
                assert isinstance(call["function"]["arguments"], str)
                assert isinstance(json.loads(call["function"]["arguments"]), dict)
        self.calls += 1
        self.received.append(json.loads(json.dumps(messages)))
        for chunk in self._chunks:
            yield chunk


def _record(
    run_ref: RunRef,
    decision_step_id: str,
    *tools: tuple[str, dict[str, Any]],
    accepted_output: dict[str, Any] | None = None,
    schema_version: int = ACTION_DECISION_SCHEMA_VERSION,
) -> AcceptedDecisionRecord:
    actions = tuple(
        AcceptedAction(action_key=f"key-{position}", tool=tool, arguments=args)
        for position, (tool, args) in enumerate(tools)
    )
    record = AcceptedDecisionRecord(
        run_ref=run_ref,
        decision_step_id=decision_step_id,
        input_refs=dict(REFS),
        accepted_output=dict(accepted_output) if accepted_output is not None else {"text": "go"},
        actions=actions,
        schema_version=schema_version,
    )
    return replace(record, accepted_at="2026-01-01T00:00:00+00:00", content_digest=canonical_decision_digest(record))


def _outcomes(
    record: AcceptedDecisionRecord,
    statuses: tuple[str, ...],
    *,
    content: str = "governed-result",
    results: list[dict[str, Any] | None] | None = None,
) -> tuple[AcceptedActionOutcome, ...]:
    outcomes = []
    # strict=False: the mismatch tests intentionally pass fewer statuses than actions.
    for position, (action, status) in enumerate(zip(record.actions, statuses, strict=False)):
        execution_id = execution_id_for_action(record.run_ref, record.decision_step_id, action.action_key)
        if results is not None and results[position] is not None:
            result = results[position]
        else:
            result = {
                "role": "tool",
                "tool_call_id": tool_call_id_for_action(record.run_ref, record.decision_step_id, action.action_key),
                "content": content,
            }
        outcomes.append(AcceptedActionOutcome(status, execution_id, result=result))
    return tuple(outcomes)


def _turn_outcome(record: AcceptedDecisionRecord, outcomes: tuple[AcceptedActionOutcome, ...]) -> AgentTurnOutcome:
    return AgentTurnOutcome(record, False, AcceptedActionBatchReport(outcomes, ()))


# ---------------------------------------------------------------------------
# Projection: happy paths
# ---------------------------------------------------------------------------


async def test_multi_action_turn_projects_paired_messages() -> None:
    """Two succeeded actions rebuild one assistant call plus ordered results."""
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}), ("web_search", {"q": 2}))
    outcome = _turn_outcome(record, _outcomes(record, ("succeeded", "succeeded")))

    projected = project_turn_messages(outcome)

    first_id = tool_call_id_for_action(run_ref, "step-1", "key-0")
    second_id = tool_call_id_for_action(run_ref, "step-1", "key-1")
    assistant, first_tool, second_tool = projected
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "go"
    assert [call["id"] for call in assistant["tool_calls"]] == [first_id, second_id]
    assert [call["function"]["name"] for call in assistant["tool_calls"]] == ["biz_tool", "web_search"]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"q": 1}
    assert (first_tool, second_tool) == (
        {"role": "tool", "tool_call_id": first_id, "content": "governed-result"},
        {"role": "tool", "tool_call_id": second_id, "content": "governed-result"},
    )


async def test_governed_placeholder_is_projected_verbatim() -> None:
    """A first-run blocked action projects the governed placeholder only."""
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    governed = {
        "role": "tool",
        "tool_call_id": tool_call_id_for_action(run_ref, "step-1", "key-0"),
        "content": "Result sanitized: policy",
    }
    outcome = _turn_outcome(record, _outcomes(record, ("succeeded",), results=[governed]))

    projected = project_turn_messages(outcome)

    assert projected[1] == {"role": "tool", "tool_call_id": governed["tool_call_id"], "content": governed["content"]}
    assert "raw" not in json.dumps(projected)


async def test_projection_does_not_mutate_inputs() -> None:
    """Outcome, record and base messages are deep-copied, never touched."""
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"nested": {"deep": [1, 2]}}))
    outcome = _turn_outcome(record, _outcomes(record, ("succeeded",)))
    base = [{"role": "user", "content": [{"type": "text", "text": "run"}]}]
    outcome_snapshot, record_snapshot, base_snapshot = (
        copy.deepcopy(outcome),
        copy.deepcopy(record),
        copy.deepcopy(base),
    )

    projected = project_turn_messages(outcome, base)
    projected[0]["content"][0]["text"] = "mutated"
    projected[1]["tool_calls"][0]["function"]["arguments"] = '{"mutated": true}'
    projected[2]["content"] = "mutated"

    assert outcome == outcome_snapshot and record == record_snapshot and base == base_snapshot
    fresh = project_turn_messages(outcome, base)
    assert json.loads(fresh[1]["tool_calls"][0]["function"]["arguments"]) == {"nested": {"deep": [1, 2]}}
    assert fresh[2]["content"] == "governed-result"


async def test_v1_record_without_text_falls_back_to_empty_content() -> None:
    """Historical v1 action decisions carry no text field; project anyway."""
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}), accepted_output={"legacy": True})
    outcome = _turn_outcome(record, _outcomes(record, ("succeeded",)))

    projected = project_turn_messages(outcome)

    assert projected[0]["content"] == ""
    assert projected[0]["tool_calls"][0]["function"]["name"] == "biz_tool"


async def test_recovered_turn_projects_actual_governed_content() -> None:
    """A recovered turn keeps its own governed content — no byte equality."""
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    outcomes = _outcomes(record, ("succeeded",), content="re-governed-from-ledger")
    outcome = AgentTurnOutcome(record, True, AcceptedActionBatchReport(outcomes, ()))

    projected = project_turn_messages(outcome)

    assert outcome.resumed is True
    assert projected[1]["content"] == "re-governed-from-ledger"


# ---------------------------------------------------------------------------
# Projection: explicit rejection matrix
# ---------------------------------------------------------------------------


async def test_final_answer_turn_is_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", schema_version=FINAL_ANSWER_SCHEMA_VERSION)
    with pytest.raises(DecisionValidationError, match="final answer"):
        project_turn_messages(_turn_outcome(record, ()))


@pytest.mark.parametrize("status", ["failed", "withheld", "stopped"])
async def test_non_succeeded_action_is_rejected(status: str) -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}), ("web_search", {"q": 2}))
    outcomes = _outcomes(record, ("succeeded", status))

    with pytest.raises(DecisionValidationError, match=status):
        project_turn_messages(_turn_outcome(record, outcomes))


async def test_partially_dispatched_turn_is_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}), ("web_search", {"q": 2}))
    outcomes = _outcomes(record, ("succeeded",))[:1]
    report = AcceptedActionBatchReport(outcomes, ("key-1",))

    with pytest.raises(DecisionValidationError, match="partially dispatched"):
        project_turn_messages(AgentTurnOutcome(record, False, report))


async def test_outcome_count_mismatch_is_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}), ("web_search", {"q": 2}))
    outcomes = _outcomes(record, ("succeeded",))[:1]

    with pytest.raises(DecisionValidationError, match="partially dispatched"):
        project_turn_messages(AgentTurnOutcome(record, False, AcceptedActionBatchReport(outcomes, ())))


async def test_execution_id_mismatch_is_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    outcomes = list(_outcomes(record, ("succeeded",)))
    outcomes[0] = AcceptedActionOutcome("succeeded", "wrong-execution-id", result=outcomes[0].result)

    with pytest.raises(DecisionValidationError, match="execution_id"):
        project_turn_messages(_turn_outcome(record, tuple(outcomes)))


async def test_out_of_order_outcomes_are_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}), ("web_search", {"q": 2}))
    outcomes = list(_outcomes(record, ("succeeded", "succeeded")))
    outcomes.reverse()

    with pytest.raises(DecisionValidationError, match="execution_id"):
        project_turn_messages(_turn_outcome(record, tuple(outcomes)))


async def test_embedded_tool_call_id_mismatch_is_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    forged = {"role": "tool", "tool_call_id": "forged-id", "content": "governed-result"}

    with pytest.raises(DecisionValidationError, match="governed tool message"):
        project_turn_messages(_turn_outcome(record, _outcomes(record, ("succeeded",), results=[forged])))


async def test_malformed_result_shape_is_rejected() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    outcomes = list(_outcomes(record, ("succeeded",)))
    outcomes[0] = AcceptedActionOutcome("succeeded", outcomes[0].execution_id, result=None)

    with pytest.raises(DecisionValidationError, match="governed tool message"):
        project_turn_messages(_turn_outcome(record, tuple(outcomes)))


async def test_duplicate_derived_call_ids_are_rejected() -> None:
    run_ref = _run_ref()
    record = AcceptedDecisionRecord(
        run_ref=run_ref,
        decision_step_id="step-1",
        input_refs=dict(REFS),
        accepted_output={"text": "go"},
        actions=(
            AcceptedAction(action_key="same-key", tool="biz_tool", arguments={"q": 1}),
            AcceptedAction(action_key="same-key", tool="web_search", arguments={"q": 2}),
        ),
    )
    outcomes = _outcomes(record, ("succeeded", "succeeded"))

    with pytest.raises(DecisionValidationError, match="duplicate action key"):
        project_turn_messages(_turn_outcome(record, outcomes))


# ---------------------------------------------------------------------------
# Responder continuation entry and the two-turn chain
# ---------------------------------------------------------------------------


async def test_two_chained_turns_deliver_projected_results_to_second_call() -> None:
    """The second AgentTurn's model call sees the first turn's tool results."""
    run_ref, session = _run_ref(), uuid.uuid4()
    tool_port, ledger = StubToolPort(), StubLedger()
    model_port_1 = StubStructuredPort(
        [
            {
                "content": None,
                "tool_calls": [
                    {"id": "ephemeral-1", "type": "function", "function": {"name": "biz_tool", "arguments": {"q": 1}}},
                    {
                        "id": "ephemeral-2",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": {"q": 2}},
                    },
                ],
            }
        ]
    )
    responder_1 = PortDecisionResponder(
        model_port_1,
        messages=[{"role": "user", "content": "run the task"}],
        config={},
        tool_definitions=TOOL_SNAPSHOT,
        expected_refs=dict(REFS),
    )
    executor_1 = ToolCallExecutor(port=tool_port, event_store=InMemoryEventStore(), action_hook=ledger)
    outcome_1 = await AgentTurn(InMemoryDecisionStore(), responder_1, executor_1).run(
        run_ref=run_ref,
        decision_step_id="step-1",
        input_refs=dict(REFS),
        ownership=STUB_OWNER,
        context={"session_id": str(session)},
        execution_context={"session_id": session},
    )
    assert len(outcome_1.report.outcomes) == 2
    assert all(item.status == "succeeded" for item in outcome_1.report.outcomes)
    assert len(tool_port.calls) == 2

    projected = project_turn_messages(outcome_1)

    model_port_2 = StubStructuredPort([{"content": "all results consumed"}])
    responder_2 = PortDecisionResponder.for_turn_messages(
        model_port_2,
        messages=projected,
        config={},
        tool_definitions=TOOL_SNAPSHOT,
        expected_refs={"config": "v1", "turn": 2},
    )
    outcome_2 = await AgentTurn(InMemoryDecisionStore(), responder_2, executor_1).run(
        run_ref=run_ref,
        decision_step_id="step-2",
        input_refs={"config": "v1", "turn": 2},
        ownership=STUB_OWNER,
        context={"session_id": str(session)},
        execution_context={"session_id": session},
    )

    assert outcome_2.is_final_answer is True
    assert model_port_2.calls == 1
    received = model_port_2.received[0]
    assistant = received[0]
    assert assistant["role"] == "assistant"
    assert len(assistant["tool_calls"]) == 2
    results = [message for message in received[1:]]
    assert [message["role"] for message in results] == ["tool", "tool"]
    for message, action in zip(results, outcome_1.record.actions, strict=True):
        assert message["tool_call_id"] == tool_call_id_for_action(run_ref, "step-1", action.action_key)
        assert message["content"] == str({"ok": True, "by": action.tool})
    assert len(tool_port.calls) == 2  # the second turn dispatched nothing


async def test_continuation_entry_freezes_projected_messages() -> None:
    """Mutating the caller's list after construction cannot reach the model."""
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    projected = project_turn_messages(_turn_outcome(record, _outcomes(record, ("succeeded",))))
    model_port = StubStructuredPort([{"content": "seen"}])
    responder = PortDecisionResponder.for_turn_messages(
        model_port,
        messages=projected,
        config={},
        tool_definitions=TOOL_SNAPSHOT,
        expected_refs=dict(REFS),
    )

    projected.append({"role": "user", "content": "late injection"})
    proposal = await responder.respond(run_ref, "step-2", dict(REFS))

    assert proposal.accepted_output == {"text": "seen"}
    assert len(model_port.received[0]) == 2  # assistant + tool result, no injection


async def test_continuation_entry_rejects_mismatched_refs_without_model_call() -> None:
    run_ref = _run_ref()
    record = _record(run_ref, "step-1", ("biz_tool", {"q": 1}))
    projected = project_turn_messages(_turn_outcome(record, _outcomes(record, ("succeeded",))))
    model_port = StubStructuredPort([{"content": "seen"}])
    responder = PortDecisionResponder.for_turn_messages(
        model_port,
        messages=projected,
        config={},
        tool_definitions=TOOL_SNAPSHOT,
        expected_refs=dict(REFS),
    )

    with pytest.raises(DecisionValidationError, match="input refs"):
        await responder.respond(run_ref, "step-2", {"config": "v2"})

    assert model_port.calls == 0


async def test_existing_constructor_unchanged() -> None:
    """The pre-continuation construction path behaves exactly as before."""
    model_port = StubStructuredPort([{"content": "plain answer"}])
    responder = PortDecisionResponder(
        model_port,
        messages=[{"role": "user", "content": "hi"}],
        config={},
        tool_definitions=TOOL_SNAPSHOT,
        expected_refs=dict(REFS),
    )
    proposal = await responder.respond(_run_ref(), "step-1", dict(REFS))

    assert proposal.accepted_output == {"text": "plain answer"}
    assert model_port.calls == 1


@pytest.mark.parametrize("fault", ["uncommitted", "digest", "arguments", "unknown_version", "empty_actions"])
def test_projection_rejects_invalid_or_changed_accepted_record(fault):
    """The projection cannot promote a draft or corrupted decision into model history."""
    record = _record(_run_ref(), "step", ("biz_tool", {"q": 1}))
    if fault == "uncommitted":
        record = replace(record, accepted_at=None, content_digest=None)
    elif fault == "digest":
        record = replace(record, content_digest="0" * 64)
    elif fault == "arguments":
        record.actions[0].arguments["q"] = 999
    elif fault == "unknown_version":
        record = replace(record, schema_version=99)
    else:
        record = replace(record, actions=())
    with pytest.raises(DecisionValidationError):
        project_turn_messages(_turn_outcome(record, _outcomes(record, tuple("succeeded" for _ in record.actions))))


def test_projection_uses_provider_compatible_json_arguments():
    """The next model receives JSON text, as required by the function-call wire format."""
    arguments = {"query": "中文", "nested": {"flag": True, "value": None, "items": [1, "two"]}}
    record = _record(_run_ref(), "step", ("biz_tool", arguments))
    projected = project_turn_messages(_turn_outcome(record, _outcomes(record, ("succeeded",))))
    encoded = projected[0]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(encoded, str)
    assert json.loads(encoded) == arguments
    assert record.actions[0].arguments == arguments


def test_projection_rejects_error_marked_succeeded_result():
    """An error marker must not disappear when projecting a contradictory success."""
    record = _record(_run_ref(), "step", ("biz_tool", {}))
    outcomes = _outcomes(record, ("succeeded",))
    assert outcomes[0].result is not None
    result = {**outcomes[0].result, "is_error": True}
    with pytest.raises(DecisionValidationError):
        project_turn_messages(_turn_outcome(record, (replace(outcomes[0], result=result),)))


@pytest.mark.parametrize(
    "base", [["not a message"], [{"role": "user", "content": float("nan")}], [{"role": "user", "content": {"invalid"}}]]
)
def test_projection_rejects_non_json_base_messages(base):
    """The pure projection rejects malformed model input before a responder is constructed."""
    record = _record(_run_ref(), "step", ("biz_tool", {}))
    with pytest.raises(DecisionValidationError):
        project_turn_messages(_turn_outcome(record, _outcomes(record, ("succeeded",))), base)


@pytest.mark.parametrize("action", [GuardrailAction.BLOCK, GuardrailAction.SANITIZE])
async def test_real_turn_recovery_projects_governed_results_without_repeating_tools(action):
    """Recover through AgentTurn and the ledger before supplying the next model."""

    class StubResultHook(PostToolHook):
        """Expose only the policy-controlled view of the business result."""

        async def on_post_tool_call(self, name, result, context):
            """Apply the same policy to the first result and its persisted replay."""
            return GuardrailResult(action=action, reason="test policy", modified_data={"result": "masked-result"})

    run_ref, store, ledger, tool_port = _run_ref(), InMemoryDecisionStore(), StubLedger(), StubToolPort()
    model = StubStructuredPort(
        [
            {
                "tool_calls": [
                    {"id": "provider-1", "function": {"name": "biz_tool", "arguments": {"q": 1}}},
                    {"id": "provider-2", "function": {"name": "web_search", "arguments": {"q": 2}}},
                ]
            }
        ]
    )
    base = [{"role": "user", "content": "use the tools"}]
    responder = PortDecisionResponder(
        model, messages=base, config={}, tool_definitions=TOOL_SNAPSHOT, expected_refs=REFS
    )
    first = await AgentTurn(
        store,
        responder,
        ToolCallExecutor(
            tool_port,
            action_hook=ledger,
            post_tool_hook=StubResultHook(),
        ),
    ).run(run_ref=run_ref, decision_step_id="step-1", input_refs=REFS, ownership=STUB_OWNER)
    assert model.calls == 1 and len(tool_port.calls) == 2
    claims_before, states_before = len(ledger.claims), copy.deepcopy(ledger.states)

    recovery_model = StubStructuredPort([])
    recovered = await AgentTurn(
        store,
        PortDecisionResponder(
            recovery_model,
            messages=base,
            config={},
            tool_definitions=TOOL_SNAPSHOT,
            expected_refs=REFS,
        ),
        ToolCallExecutor(tool_port, action_hook=ledger, post_tool_hook=StubResultHook()),
    ).run(
        run_ref=run_ref,
        decision_step_id="step-1",
        input_refs=REFS,
        ownership=STUB_OWNER,
    )
    assert recovered.resumed is True
    assert recovered.record == first.record
    assert recovery_model.calls == 0
    assert len(tool_port.calls) == 2 and len(ledger.claims) == claims_before
    assert ledger.states == states_before
    expected_content = "Result sanitized: test policy" if action is GuardrailAction.BLOCK else "masked-result"
    projected = project_turn_messages(recovered, base)
    assert [message["content"] for message in projected[2:]] == [expected_content, expected_content]

    next_model = StubStructuredPort([{"content": "consumed governed results"}])
    next_refs = {"config": "v1", "previous_decision": recovered.record.content_digest}
    next_responder = PortDecisionResponder.for_turn_messages(
        next_model, messages=projected, config={}, tool_definitions=TOOL_SNAPSHOT, expected_refs=next_refs
    )
    next_turn = await AgentTurn(store, next_responder, ToolCallExecutor(tool_port, action_hook=ledger)).run(
        run_ref=run_ref,
        decision_step_id="step-2",
        input_refs=next_refs,
        ownership=STUB_OWNER,
    )
    assert next_turn.is_final_answer
    assert next_model.calls == 1 and next_model.received[0] == projected
    assert len(tool_port.calls) == 2 and len(ledger.claims) == claims_before
    assert ledger.states == states_before


@pytest.mark.parametrize("content", ["", "中文结果", "Result sanitized: policy"])
def test_projection_preserves_governed_content_and_drops_unrelated_metadata(content):
    """Only the governed result string crosses into the model message."""
    record = _record(_run_ref(), "step", ("biz_tool", {}))
    outcomes = _outcomes(record, ("succeeded",), content=content)
    assert outcomes[0].result is not None
    result = {**outcomes[0].result, "is_error": False, "raw_result": "DO-NOT-EXPOSE"}
    projected = project_turn_messages(_turn_outcome(record, (replace(outcomes[0], result=result),)))
    assert projected[1] == {"role": "tool", "tool_call_id": result["tool_call_id"], "content": content}
    assert "DO-NOT-EXPOSE" not in json.dumps(projected)
