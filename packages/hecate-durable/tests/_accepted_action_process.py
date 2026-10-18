"""Independent process probe for persisted accepted-action dispatch."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.accepted_action_step import AcceptedActionStep
from hecate_runtime.accepted_decision import DecisionOwnership, RunRef
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor


class StubBusinessPort(StubRuntimePort):
    """Log a durable stand-in business effect; recovery forbids execution."""

    async def tool_execute(self, name, args, context=None):
        if sys.argv[2] == "read":
            raise AssertionError("completed action re-executed")
        with open(sys.argv[3], "a", encoding="utf-8") as stream:
            stream.write("effect\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"business": args["q"]}

    async def llm_invoke_structured(self, messages, config):
        raise AssertionError("accepted-action recovery called a model")
        yield


async def main() -> None:
    """Read the original decision and resume using only SQL-persisted facts."""
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
    result = await AcceptedActionStep(
        SqlAcceptedDecisionStore(backend), ToolCallExecutor(port=StubBusinessPort(), action_hook=hook)
    ).dispatch(run_ref=run, decision_step_id="step-1", action_key="action-1", ownership=owner)
    assert result.status == "succeeded", result
    assert result.result is not None
    print(json.dumps({"execution_id": result.execution_id, "content": result.result["content"]}), flush=True)
    # Do not allow normal Python teardown to carry process-local state forward.
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
