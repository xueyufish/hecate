"""Independent process probe for persisted accepted-decision turn recovery.

Modes:
- ``commit-crash`` — the decision is committed (the responder produced the
  proposal) and the process hard-exits when the first action's record load
  arrives: a true worker crash after acceptance, before any claim.
- ``recover-fresh`` — recover through the same turn entry; the responder
  must never be called and every action executes fresh.
- ``a1-crash`` — action-1 executes with a durable effect and ledger
  outcome; the process hard-exits at action-2's record load.
- ``recover-after-a1`` — recovery must replay action-1 from the ledger
  (its business port is never invoked) and execute action-2/action-3.
- ``receipt-loss`` — A2's effect lands but its outcome write fails.
- ``recover-unknown`` — another interpreter must stop at A2 without calling
  any business tool or model and must leave A3 undispatched.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime

from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.accepted_decision import AcceptedAction, DecisionOwnership, RunRef
from hecate_runtime.agent_turn import AgentTurn
from hecate_runtime.decision_step import DecisionProposal, DecisionResponder
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor

_MODE = sys.argv[2]
_EFFECT = sys.argv[3]
_RECOVERING = _MODE.startswith("recover")


class StubBusinessPort(StubRuntimePort):
    """Record per-action durable effects; recovery forbids completed work."""

    async def tool_execute(self, name, args, context=None):
        action_key = args["action_key"]
        if _MODE == "recover-unknown" or (_MODE == "recover-after-a1" and action_key == "action-1"):
            raise AssertionError("completed action re-executed during recovery")
        with open(_EFFECT, "a", encoding="utf-8") as stream:
            stream.write(f"effect-{action_key}\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"business": action_key}

    async def llm_invoke_structured(self, messages, config):
        raise AssertionError("accepted-decision turn recovery called a model")
        yield


class StubTurnResponder(DecisionResponder):
    """One fixed proposal for a fresh turn; forbidden once recovering."""

    async def respond(self, run_ref: RunRef, decision_step_id: str, input_refs: dict) -> DecisionProposal:
        assert not _RECOVERING, "recovery resolved from storage and must not re-ask the model"
        return DecisionProposal(
            accepted_output={"text": "accepted"},
            actions=(
                AcceptedAction("action-1", "memory_add", {"action_key": "action-1"}),
                AcceptedAction("action-2", "memory_add", {"action_key": "action-2"}),
                AcceptedAction("action-3", "web_search", {"action_key": "action-3"}),
            ),
        )


class StubCrashOnNextLookupStore(SqlAcceptedDecisionStore):
    """Hard-exit at the first lookup after the crash point is durably set.

    The turn adds one lookup ahead of the batch (decision phase), so
    exit_after=2 crashes after the commit but before the first claim,
    and exit_after=3 crashes after action-1's effect and outcome.
    """

    def __init__(self, backend, lookups_before_exit: int) -> None:
        super().__init__(backend)
        self._lookups = 0
        self._exit_after = lookups_before_exit

    async def lookup(self, run_ref, decision_step_id):
        self._lookups += 1
        if self._lookups > self._exit_after:
            os._exit(9)
        return await super().lookup(run_ref, decision_step_id)


async def main() -> None:
    """Run the turn from SQL-persisted facts only."""
    data = json.loads(sys.argv[1])
    backend = SqlDurableStore(data["url"], lease_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    owner = DecisionOwnership(**data["ownership"])
    run = RunRef("domain", "run-1")
    hook = SqlActionLedgerHook(
        backend,
        task_ref=BackendRef(RefKind.TASK, "domain", "task-run-1"),
        run_ref=BackendRef(RefKind.RUN, "domain", "run-1"),
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    store: SqlAcceptedDecisionStore = (
        StubCrashOnNextLookupStore(backend, lookups_before_exit=2 if _MODE == "commit-crash" else 3)
        if _MODE.endswith("-crash")
        else SqlAcceptedDecisionStore(backend)
    )
    if _MODE == "receipt-loss":
        record_outcome = backend.record_outcome_ex
        writes = 0

        def fail_second_receipt(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise RuntimeError("receipt lost after the business effect")
            return record_outcome(*args, **kwargs)

        backend.record_outcome_ex = fail_second_receipt
    turn = AgentTurn(store, StubTurnResponder(), ToolCallExecutor(port=StubBusinessPort(), action_hook=hook))
    outcome = await turn.run(run_ref=run, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner)
    assert outcome.resumed is _RECOVERING, outcome
    if _MODE in {"receipt-loss", "recover-unknown"}:
        assert [o.status for o in outcome.report.outcomes] == ["succeeded", "stopped"], outcome.report
        assert outcome.report.unprocessed_action_keys == ("action-3",), outcome.report
    else:
        assert outcome.report.unprocessed_action_keys == (), outcome.report
    print(
        json.dumps(
            {
                "statuses": [o.status for o in outcome.report.outcomes],
                "resumed": outcome.resumed,
                "record": outcome.record.to_dict(),
                **asdict(outcome.report),
            }
        ),
        flush=True,
    )
    # Do not allow normal Python teardown to carry process-local state forward.
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
