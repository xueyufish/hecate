"""Independent process probe for persisted accepted-decision batch recovery.

Modes:
- ``write-crash`` — run the batch; action-1 executes with a durable effect
  and its ledger outcome is recorded; the process hard-exits at the next
  decision lookup (before action-2 is ever claimed): a true worker crash
  between two actions.
- ``continue`` — run the full batch: action-1 must replay from the ledger
  (its business port is never invoked), then action-2/action-3 execute.
- ``race-first`` / ``race-second`` — overlap two interpreters while A1 is
  claimed without a receipt; the second must stop before producing effects.
- ``recover-all`` — recover all recorded outcomes with every tool call forbidden.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.accepted_action_batch import AcceptedActionBatch
from hecate_runtime.accepted_action_step import AcceptedActionStep
from hecate_runtime.accepted_decision import DecisionOwnership, RunRef
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor

_MODE = sys.argv[2]
_EFFECT = sys.argv[3]


class StubBusinessPort(StubRuntimePort):
    """Record per-action durable effects; recovery forbids action-1 work."""

    async def tool_execute(self, name, args, context=None):
        action_key = args["action_key"]
        if _MODE == "recover-all" or (_MODE == "continue" and action_key == "action-1"):
            raise AssertionError("completed action re-executed during recovery")
        with open(_EFFECT, "a", encoding="utf-8") as stream:
            stream.write(f"effect-{action_key}\n")
            stream.flush()
            os.fsync(stream.fileno())
        if _MODE == "race-first" and action_key == "action-1":
            # Keep A1 genuinely claimed while another interpreter attempts it.
            ready = Path(_EFFECT + ".ready")
            release = Path(_EFFECT + ".release")
            ready.write_text("claimed", encoding="utf-8")
            async with asyncio.timeout(30):
                while not release.exists():
                    await asyncio.sleep(0.02)
        return {"business": action_key}

    async def llm_invoke_structured(self, messages, config):
        raise AssertionError("accepted-decision batch recovery called a model")
        yield


class StubCrashOnNextLookupStore(SqlAcceptedDecisionStore):
    """Hard-exit at the first lookup after the crash point is durably set."""

    def __init__(self, backend, lookups_before_exit: int) -> None:
        super().__init__(backend)
        self._lookups = 0
        self._exit_after = lookups_before_exit

    async def lookup(self, run_ref, decision_step_id):
        self._lookups += 1
        if self._lookups > self._exit_after:
            # Simulate a worker crash between two actions: everything before
            # this point (action-1's effect AND ledger outcome) is durable.
            os._exit(9)
        return await super().lookup(run_ref, decision_step_id)


async def main() -> None:
    """Run the batch from SQL-persisted facts only."""
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
        StubCrashOnNextLookupStore(backend, lookups_before_exit=2)
        if _MODE == "write-crash"
        else SqlAcceptedDecisionStore(backend)
    )
    step = AcceptedActionStep(store, ToolCallExecutor(port=StubBusinessPort(), action_hook=hook))
    report = await AcceptedActionBatch(step, store).run(run_ref=run, decision_step_id="step-1", ownership=owner)
    if _MODE == "race-second":
        assert [o.status for o in report.outcomes] == ["stopped"], report
        assert report.unprocessed_action_keys == ("action-2", "action-3"), report
    else:
        assert report.unprocessed_action_keys == (), report
    print(json.dumps({"statuses": [o.status for o in report.outcomes], **asdict(report)}), flush=True)
    # Do not allow normal Python teardown to carry process-local state forward.
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
