"""One recoverable agent turn: accept a decision, then dispatch its actions.

AgentTurn is pure composition over the accepted-decision stack. One call
processes exactly one ``(run_ref, decision_step_id)`` pair in a fixed
order — find-or-accept the decision first, then serially dispatch the
committed actions. The record digest returned by the decision phase pins
the action phase: a record swapped in between acceptance and dispatch is
a conflict, never new facts. Recovery re-enters through the same call;
the decision lookup hits first, so a turn that died after commit resumes
with zero model calls. The turn speaks only for itself: its outcome
never advances a Task, triggers replanning or decides completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hecate_runtime.accepted_action_batch import AcceptedActionBatch, AcceptedActionBatchReport
from hecate_runtime.accepted_action_step import AcceptedActionStep
from hecate_runtime.accepted_decision import (
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionOwnership,
    DecisionValidationError,
    RunRef,
)
from hecate_runtime.decision_step import DecisionResponder, DecisionStep
from hecate_runtime.tool_call_executor import ToolCallExecutor

__all__ = ["AgentTurn", "AgentTurnOutcome"]


@dataclass(frozen=True)
class AgentTurnOutcome:
    """Facts of one turn: the accepted record, how it was obtained, the report.

    ``record`` is the committed accepted decision; ``resumed`` distinguishes
    a lookup hit (recovery) from a fresh model-proposed commit; ``report``
    carries per-action outcomes plus unprocessed keys. None of these are
    completion facts — what the run or task should do next is the host's
    decision, not the turn's.
    """

    record: AcceptedDecisionRecord
    resumed: bool
    report: AcceptedActionBatchReport


class AgentTurn:
    """Compose decision acceptance and governed action dispatch for one turn.

    The stack components share one injected :class:`AcceptedDecisionStore`
    so the decision and action phases read the same storage; the responder
    and the executor (with its bound governance chain) are the host's
    existing capabilities. The same ownership instance is used for both
    the decision commit and every action dispatch; the turn neither
    synthesizes nor swaps leases.
    """

    def __init__(
        self,
        store: AcceptedDecisionStore,
        responder: DecisionResponder,
        executor: ToolCallExecutor,
    ) -> None:
        self._store = store
        self._decision_step = DecisionStep(store, responder)
        self._action_batch = AcceptedActionBatch(AcceptedActionStep(store, executor), store)

    async def run(
        self,
        *,
        run_ref: RunRef,
        decision_step_id: str,
        input_refs: dict[str, Any] | None = None,
        ownership: DecisionOwnership | None = None,
        context: dict[str, Any] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> AgentTurnOutcome:
        """Accept the decision if absent, then dispatch its committed actions."""
        if not isinstance(run_ref, RunRef):
            raise DecisionValidationError("run_ref must be a RunRef")
        # Reject malformed dispatch inputs before incurring model usage or
        # committing a decision that the action phase cannot consume.
        for name, value in (("context", context), ("execution_context", execution_context)):
            if value is not None and not isinstance(value, dict):
                raise DecisionValidationError(f"{name} must be a dictionary or None")
        if ownership is not None and not isinstance(ownership, DecisionOwnership):
            raise DecisionValidationError("ownership must be a DecisionOwnership or None")
        outcome = await self._decision_step.execute(
            run_ref=run_ref,
            decision_step_id=decision_step_id,
            input_refs=input_refs,
            ownership=ownership,
        )
        report = await self._action_batch.run(
            run_ref=run_ref,
            decision_step_id=decision_step_id,
            ownership=ownership,
            context=context,
            execution_context=execution_context,
            expected_decision_digest=outcome.record.content_digest,
        )
        return AgentTurnOutcome(outcome.record, outcome.resumed, report)
