"""One graph-independent accepted-decision step, not a complete Controller.

The responder supplies accepted-visible output with action intent or a
final answer, never hidden reasoning. This component dispatches no tools
or approval requests.
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, replace
from typing import Any

from hecate_runtime.accepted_decision import (
    ACTION_DECISION_SCHEMA_VERSION,
    FINAL_ANSWER_SCHEMA_VERSION,
    AcceptedAction,
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    canonical_decision_digest,
    canonical_json,
    snapshot_decision,
)

__all__ = [
    "KIND_ACTIONS",
    "KIND_FINAL_ANSWER",
    "AcceptedDecisionOutcome",
    "DecisionProposal",
    "DecisionResponder",
    "DecisionStep",
    "DecisionValidationError",
]

KIND_ACTIONS = "actions"
KIND_FINAL_ANSWER = "final_answer"
_PROPOSAL_SCHEMA_VERSIONS = {
    KIND_ACTIONS: ACTION_DECISION_SCHEMA_VERSION,
    KIND_FINAL_ANSWER: FINAL_ANSWER_SCHEMA_VERSION,
}


@dataclass(frozen=True)
class DecisionProposal:
    """Validated visible output and proposed actions, not yet accepted.

    ``kind`` discriminates an action proposal from a final-answer proposal;
    the decision step maps it to the record's schema version, and record
    validation enforces the matching content shape before any commit.
    """

    accepted_output: dict[str, Any]
    actions: tuple[AcceptedAction, ...]
    kind: str = KIND_ACTIONS

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in _PROPOSAL_SCHEMA_VERSIONS:
            raise DecisionValidationError("decision proposal kind must be 'actions' or 'final_answer'")


class DecisionResponder(abc.ABC):
    """Injected model capability; caller accounts for all attempted model usage."""

    @abc.abstractmethod
    async def respond(self, run_ref: RunRef, decision_step_id: str, input_refs: dict[str, Any]) -> DecisionProposal:
        """Propose output/actions without deciding their acceptance or dispatch."""


@dataclass(frozen=True)
class AcceptedDecisionOutcome:
    """Accepted record and whether it was found before asking the model."""

    record: AcceptedDecisionRecord
    resumed: bool


class DecisionStep:
    """Find an original decision or ask once and return only after commit."""

    def __init__(self, store: AcceptedDecisionStore, responder: DecisionResponder) -> None:
        self._store = store
        self._responder = responder

    async def execute(
        self,
        *,
        run_ref: RunRef,
        decision_step_id: str,
        input_refs: dict[str, Any] | None = None,
        ownership: DecisionOwnership | None = None,
    ) -> AcceptedDecisionOutcome:
        """Recover matching input versions or atomically accept a new proposal."""
        if not isinstance(input_refs, (dict, type(None))):
            raise DecisionValidationError("input_refs must be a JSON object")
        refs = json.loads(canonical_json(input_refs if input_refs is not None else {}))
        if not isinstance(decision_step_id, str) or not decision_step_id.strip() or len(decision_step_id) > 256:
            raise DecisionValidationError("decision_step_id must be nonblank and at most 256 characters")
        existing = await self._store.lookup(run_ref, decision_step_id)
        if existing is not None:
            existing = snapshot_decision(existing)
            if existing.run_ref != run_ref or existing.decision_step_id != decision_step_id:
                raise DecisionStoreUnavailableError("lookup returned a decision for another identity")
            digest = canonical_decision_digest(existing)
            if existing.content_digest != digest or not existing.accepted_at:
                raise DecisionStoreUnavailableError("lookup returned an uncommitted or corrupt decision")
            if canonical_json(existing.input_refs) != canonical_json(refs):
                raise DecisionConflictError(
                    decision_step_id,
                    existing.content_digest,
                    canonical_decision_digest(replace(existing, input_refs=refs)),
                )
            return AcceptedDecisionOutcome(existing, resumed=True)

        # Freeze the requested version references before awaiting untrusted model work.
        proposal = await self._responder.respond(run_ref, decision_step_id, json.loads(canonical_json(refs)))
        if not isinstance(proposal, DecisionProposal):
            raise DecisionValidationError("responder must return a DecisionProposal")
        # The proposal kind selects the record version; snapshot_decision then
        # rejects kind/content mismatches (e.g. final answer with actions).
        record = snapshot_decision(
            AcceptedDecisionRecord(
                run_ref,
                decision_step_id,
                refs,
                proposal.accepted_output,
                proposal.actions,
                schema_version=_PROPOSAL_SCHEMA_VERSIONS[proposal.kind],
            )
        )
        committed = snapshot_decision(await self._store.commit(record, ownership))
        if (
            committed.content_digest != canonical_decision_digest(record)
            or committed.content_digest != canonical_decision_digest(committed)
            or not committed.accepted_at
        ):
            raise DecisionStoreUnavailableError("commit did not return the accepted original decision")
        return AcceptedDecisionOutcome(committed, resumed=False)
