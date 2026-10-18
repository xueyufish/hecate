"""Serially execute the ordered actions of one committed accepted decision.

The batch is pure orchestration over the single-action dispatch path: the
actions, arguments and order come exclusively from the committed record,
and recovery is the same re-dispatch path (the ledger replays recorded
results for completed actions and stops on unknown outcomes). The batch
keeps no cursor of its own and never advances a Task or triggers
replanning — its report speaks only for the actions it processed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hecate_runtime.accepted_action_step import (
    AcceptedActionLookupError,
    AcceptedActionOutcome,
    AcceptedActionStep,
    execution_id_for_action,
    load_verified_record,
)
from hecate_runtime.accepted_decision import (
    AcceptedDecisionStore,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    StaleDecisionFenceError,
)


@dataclass(frozen=True)
class AcceptedActionBatchReport:
    """Outcome of one serial pass over a committed decision's action list.

    ``outcomes`` preserves the persisted action order for every dispatched
    action — recovered actions carry their ledger-backed real results.
    ``unprocessed_action_keys`` lists the persisted-order keys that were
    not dispatched because a terminal outcome stopped the pass. The report
    speaks only for these actions: it never advances a Task, never
    triggers replanning, and is not a completion decision.
    """

    outcomes: tuple[AcceptedActionOutcome, ...] = ()
    unprocessed_action_keys: tuple[str, ...] = ()


class AcceptedActionBatch:
    """Serially dispatch each persisted action of one accepted decision.

    Actions, arguments and order come exclusively from the committed
    record via :class:`AcceptedDecisionStore` — callers cannot inject,
    reorder or skip actions because the entry point accepts no action
    list. Every action goes through the same single-action dispatch path,
    so ownership, fencing, authorization, approval and the stable
    execution identity are enforced per action; ownership rejections after
    an earlier action convert that action to a ``stopped`` outcome and end
    the pass instead of escalating past the boundary.
    """

    def __init__(self, step: AcceptedActionStep, store: AcceptedDecisionStore) -> None:
        self._step = step
        self._store = store

    async def run(
        self,
        *,
        run_ref: RunRef,
        decision_step_id: str,
        ownership: DecisionOwnership | None = None,
        context: dict[str, Any] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> AcceptedActionBatchReport:
        """Dispatch the committed actions in order until a terminal outcome."""
        if not isinstance(run_ref, RunRef):
            raise DecisionValidationError("run_ref must be a RunRef")
        record = await load_verified_record(self._store, run_ref, decision_step_id)
        outcomes: list[AcceptedActionOutcome] = []
        for action in record.actions:
            try:
                outcome = await self._step.dispatch(
                    run_ref=run_ref,
                    decision_step_id=decision_step_id,
                    action_key=action.action_key,
                    ownership=ownership,
                    context=context,
                    execution_context=execution_context,
                    expected_decision_digest=record.content_digest,
                )
            except (
                DecisionConflictError,
                DecisionValidationError,
                StaleDecisionFenceError,
                DecisionStoreUnavailableError,
                AcceptedActionLookupError,
            ) as exc:
                # Preserve a completed prefix without disguising a storage
                # failure or substituted decision as an authorization denial.
                if isinstance(exc, DecisionConflictError):
                    reason = "decision_conflict"
                elif isinstance(exc, StaleDecisionFenceError):
                    reason = "stale_ownership"
                elif isinstance(exc, (DecisionStoreUnavailableError, AcceptedActionLookupError)):
                    reason = "decision_store_unavailable"
                else:
                    reason = "invalid_action"
                outcomes.append(
                    AcceptedActionOutcome(
                        "stopped",
                        execution_id_for_action(run_ref, decision_step_id, action.action_key),
                        stop_reason=reason,
                    )
                )
                break
            outcomes.append(outcome)
            if outcome.status != "succeeded":
                break
        unprocessed = tuple(action.action_key for action in record.actions[len(outcomes) :])
        return AcceptedActionBatchReport(tuple(outcomes), unprocessed)
