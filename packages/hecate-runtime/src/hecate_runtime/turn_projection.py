"""Project a completed action turn into the next model call's messages.

Pure, validating conversion from an :class:`AgentTurnOutcome` whose
actions all succeeded to the assistant ``tool_calls`` message and the
ordered tool-result messages a follow-up model call needs. Only governed
results are consumed — ``AcceptedActionOutcome.result`` is the
executor's post-PostTool message — and every identity is re-derived from
the committed decision facts, so a mismatched receipt is rejected
instead of projected. The projection deep-copies its inputs: neither the
outcome, the base messages, nor the committed record are mutated. A
final-answer turn, any non-succeeded action, a partially dispatched
batch, or a receipt whose identity does not match the committed facts is
explicitly rejected — never silently converted.
"""

from __future__ import annotations

import copy
from typing import Any

from hecate_runtime.accepted_action_batch import AcceptedActionBatchReport
from hecate_runtime.accepted_action_step import (
    AcceptedActionOutcome,
    execution_id_for_action,
    tool_call_id_for_action,
)
from hecate_runtime.accepted_decision import (
    FINAL_ANSWER_SCHEMA_VERSION,
    AcceptedDecisionRecord,
    DecisionValidationError,
    canonical_decision_digest,
    canonical_json,
    snapshot_decision,
)
from hecate_runtime.agent_turn import AgentTurnOutcome

__all__ = ["project_turn_messages"]


def project_turn_messages(
    outcome: AgentTurnOutcome,
    base_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the follow-up model messages for a fully succeeded action turn.

    Returns ``base_messages`` (deep-copied) followed by one assistant
    ``tool_calls`` message rebuilt from the committed decision facts and
    one tool result message per action, in the committed order. Tool
    result content is the governed message content carried by the
    outcome; first-run and recovered turns may differ in serialization
    and the projection preserves whichever governed content is present.

    Raises:
        DecisionValidationError: the turn is a final answer, its decision
            is not committed or has an invalid digest/schema, an action
            did not succeed, the batch was partially dispatched, an
            identity or result shape does not match the committed facts,
            or derived call IDs collide.
    """
    if not isinstance(outcome, AgentTurnOutcome):
        raise DecisionValidationError("outcome must be an AgentTurnOutcome")
    if not isinstance(outcome.record, AcceptedDecisionRecord):
        raise DecisionValidationError("turn record must be an AcceptedDecisionRecord")
    record = snapshot_decision(outcome.record)
    if (
        not isinstance(record.accepted_at, str)
        or not record.accepted_at.strip()
        or record.content_digest != canonical_decision_digest(record)
    ):
        raise DecisionValidationError("turn decision must be committed with an intact content digest")
    report = outcome.report
    if not isinstance(report, AcceptedActionBatchReport):
        raise DecisionValidationError("turn report must be an AcceptedActionBatchReport")
    if record.schema_version == FINAL_ANSWER_SCHEMA_VERSION:
        raise DecisionValidationError("turn is a final answer; it does not project into action follow-up input")
    actions = record.actions
    if len(report.outcomes) != len(actions) or report.unprocessed_action_keys:
        raise DecisionValidationError(
            "turn is partially dispatched; only turns whose whole action batch succeeded can project"
        )
    if base_messages is not None:
        if not isinstance(base_messages, list) or any(not isinstance(message, dict) for message in base_messages):
            raise DecisionValidationError("base_messages must be a list of JSON objects or None")
        canonical_json(base_messages)

    run_ref = record.run_ref
    decision_step_id = record.decision_step_id
    derived_ids: list[str] = []
    governed_contents: list[str] = []
    for position, (action, action_outcome) in enumerate(zip(actions, report.outcomes, strict=True)):
        if not isinstance(action_outcome, AcceptedActionOutcome):
            raise DecisionValidationError(f"action {position} outcome must be an AcceptedActionOutcome")
        if action_outcome.status != "succeeded":
            raise DecisionValidationError(
                f"action {position} ({action.action_key}) status is {action_outcome.status!r}; "
                "only fully succeeded turns project"
            )
        expected_execution_id = execution_id_for_action(run_ref, decision_step_id, action.action_key)
        if action_outcome.execution_id != expected_execution_id:
            raise DecisionValidationError(
                f"action {position} ({action.action_key}) execution_id does not match the committed decision facts"
            )
        expected_call_id = tool_call_id_for_action(run_ref, decision_step_id, action.action_key)
        if expected_call_id in derived_ids:
            raise DecisionValidationError(f"derived tool call id for action {position} collides with an earlier one")
        derived_ids.append(expected_call_id)
        result = action_outcome.result
        if (
            not isinstance(result, dict)
            or result.get("role") != "tool"
            or result.get("is_error")
            or not isinstance(result.get("content"), str)
            or result.get("tool_call_id") != expected_call_id
        ):
            raise DecisionValidationError(
                f"action {position} ({action.action_key}) result is not the governed tool message "
                f"recorded for call {expected_call_id}"
            )
        governed_contents.append(result["content"])

    text = record.accepted_output.get("text")
    assistant_message = {
        "role": "assistant",
        "content": text if isinstance(text, str) else "",
        "tool_calls": [
            {
                "id": tool_call_id_for_action(run_ref, decision_step_id, action.action_key),
                "type": "function",
                "function": {"name": action.tool, "arguments": canonical_json(action.arguments)},
            }
            for action in actions
        ],
    }
    tool_messages = [
        {"role": "tool", "tool_call_id": call_id, "content": content}
        for call_id, content in zip(derived_ids, governed_contents, strict=True)
    ]
    base = copy.deepcopy(base_messages) if base_messages is not None else []
    return base + [assistant_message, *tool_messages]
