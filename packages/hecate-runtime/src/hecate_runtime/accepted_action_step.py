"""Execute one persisted accepted action through the shared governed executor."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from hecate_runtime.accepted_decision import (
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    canonical_decision_digest,
    canonical_json,
    snapshot_decision,
)
from hecate_runtime.action_ledger import stable_execution_id
from hecate_runtime.errors import ExecutionStoppedError
from hecate_runtime.tool_call_executor import ToolCallExecutor


class AcceptedActionLookupError(LookupError):
    """The requested decision or action is absent; no dispatch may occur."""

    def __init__(self, reason: Literal["no_accepted_decision", "no_such_action"], message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class AcceptedActionOutcome:
    """One action result, independent of any Task completion decision."""

    status: Literal["succeeded", "failed", "withheld", "stopped"]
    execution_id: str
    result: dict[str, Any] | None = None
    stop_reason: str | None = None


def session_key_for_run(run_ref: RunRef) -> str:
    """Return a UUID-compatible, unambiguous run namespace across processes."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hecate:accepted-run:" + canonical_json(run_ref.to_dict())))


def tool_call_id_for_action(run_ref: RunRef, decision_step_id: str, action_key: str) -> str:
    """Scope even caller-defined action keys to their accepted decision step."""
    identity = {"run": run_ref.to_dict(), "step": decision_step_id, "action": action_key}
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hecate:accepted-action:" + canonical_json(identity)))


def execution_id_for_action(run_ref: RunRef, decision_step_id: str, action_key: str) -> str:
    """Map an accepted identity to the shared ledger's stable execution ID."""
    return stable_execution_id(
        session_key_for_run(run_ref), tool_call_id_for_action(run_ref, decision_step_id, action_key)
    )


class AcceptedActionStep:
    """Load an original action and execute it with a bound, fenced ledger.

    The executor's ledger must validate the requested Run, owning Task and
    exact dispatch lease. A plain or unconfigured executor fails closed.
    Hosts still inject the existing policy, approval and trusted principal
    context; acceptance never grants authorization.
    """

    def __init__(self, store: AcceptedDecisionStore, executor: ToolCallExecutor) -> None:
        self._store = store
        self._executor = executor

    async def dispatch(
        self,
        *,
        run_ref: RunRef,
        decision_step_id: str,
        action_key: str,
        ownership: DecisionOwnership | None = None,
        context: dict[str, Any] | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> AcceptedActionOutcome:
        """Execute or recover the selected action; never query a model."""
        if not isinstance(run_ref, RunRef):
            raise DecisionValidationError("run_ref must be a RunRef")
        for value in (decision_step_id, action_key):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise DecisionValidationError("step and action keys must be nonblank and at most 256 characters")
        record = await self._store.lookup(run_ref, decision_step_id)
        if record is None:
            raise AcceptedActionLookupError("no_accepted_decision", "no accepted decision for the requested step")
        record = self._verified(record, run_ref, decision_step_id)
        action = next((item for item in record.actions if item.action_key == action_key), None)
        if action is None:
            raise AcceptedActionLookupError("no_such_action", "action is absent from the accepted decision")
        session_id = session_key_for_run(run_ref)
        call_id = tool_call_id_for_action(run_ref, decision_step_id, action_key)
        execution_id = execution_id_for_action(run_ref, decision_step_id, action_key)
        call_context = {**(execution_context or {}), "session_id": session_id}
        tool_context = {
            **(context or {}),
            "session_id": session_id,
            "tool_call_id": call_id,
            "invocation_id": execution_id,
        }
        try:
            outcome = await self._executor.execute_accepted_call(
                {"id": call_id, "function": {"name": action.tool, "arguments": action.arguments}},
                tool_context,
                call_context,
                run_ref=run_ref,
                ownership=ownership,
                node_id="accepted-action",
            )
        except ExecutionStoppedError as exc:
            return AcceptedActionOutcome("stopped", execution_id, stop_reason=exc.reason_category)
        return AcceptedActionOutcome(outcome.status, execution_id, result=outcome.result)

    @staticmethod
    def _verified(record: AcceptedDecisionRecord, run_ref: RunRef, step: str) -> AcceptedDecisionRecord:
        try:
            if not isinstance(record, AcceptedDecisionRecord):
                raise DecisionValidationError("lookup returned an invalid record")
            record = snapshot_decision(record)
        except DecisionValidationError as exc:
            raise DecisionStoreUnavailableError("lookup returned an invalid accepted decision") from exc
        if record.run_ref != run_ref or record.decision_step_id != step:
            raise DecisionStoreUnavailableError("lookup returned a decision for another identity")
        if not record.accepted_at or record.content_digest != canonical_decision_digest(record):
            raise DecisionStoreUnavailableError("lookup returned an uncommitted or corrupt decision")
        return record
