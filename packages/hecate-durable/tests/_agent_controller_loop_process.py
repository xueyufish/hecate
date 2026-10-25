"""Independent process probe for controller-loop recovery over real SQL.

Modes (each runs the SAME controller entry against one SQLite/PostgreSQL
durable store; effects append fsync'd lines to a file so cross-process
assertions never depend on in-process state):

- ``full`` — run the whole three-turn loop; final answer, two tool effects.
- ``crash-after-decision`` — hard-exit right after turn 2's decision commit
  (before its action dispatch).
- ``crash-after-action`` — hard-exit right after turn 1's action effect and
  ledger outcome (mid-batch).
- ``recover`` — a fresh interpreter re-enters the loop: committed turns
  resume from storage with zero model calls, completed actions replay from
  the ledger with zero business effects, and the loop finishes.
- ``wait`` — turn 2's action requires approval; the callback raises the
  durable wait signal, which must escape the loop; nothing after turn 1
  runs (the probe prints ``parked`` and exits cleanly).
- ``wake`` — the approval callback now approves: the same run replays turn
  1 from storage, dispatches the approved action, and finishes.
- ``receipt-loss`` — turn 2's action effect lands but its outcome write
  fails; the loop must stop classified, not retry.
- ``recover-unknown`` — another interpreter must stop at the unknown action
  with zero new effects and zero model calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.accepted_decision import AcceptedAction, DecisionOwnership, RunRef
from hecate_runtime.agent_controller import (
    AgentController,
    ControllerLimits,
    ControllerRunSpec,
    TurnResponderSource,
)
from hecate_runtime.agent_turn import AgentTurn
from hecate_runtime.decision_step import KIND_FINAL_ANSWER, DecisionProposal, DecisionResponder
from hecate_runtime.errors import ApprovalWaitSignal
from hecate_runtime.eventstore import InMemoryEventStore
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_access import AccessDecision, ApprovalDecision
from hecate_runtime.tool_call_executor import ToolCallExecutor

_MODE = sys.argv[2]
_EFFECT = sys.argv[3]
_RECOVERING = _MODE in {"recover", "wake", "recover-unknown"}
_WAITING = _MODE in {"wait", "wake"}
_FINAL_TURN = 3

RUN = RunRef("domain", "run-loop-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
SQL_TASK = BackendRef(RefKind.TASK, RUN.issuer_domain, "task-run-loop-1")

_RESULT_FIRST = json.dumps({"business": "search-1"})
_RESULT_FIRST_REPR = str({"business": "search-1"})
_RESULT_SECOND = json.dumps({"business": "fetch-1"})
_RESULT_SECOND_REPR = str({"business": "fetch-1"})


def _record_effect(action_key: str) -> None:
    with open(_EFFECT, "a", encoding="utf-8") as stream:
        stream.write(f"effect-{action_key}\n")
        stream.flush()
        os.fsync(stream.fileno())


class BusinessPort(StubRuntimePort):
    """Real durable effects; completed work must never re-execute."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def tool_execute(self, name, args, context=None):
        key = args["action_key"]
        if _MODE == "recover-unknown" or (_MODE == "recover" and key == "loop-a1"):
            raise AssertionError("completed action re-executed during recovery")
        _record_effect(key)
        return {"business": args["business"]}

    async def llm_invoke_structured(self, messages, config):  # pragma: no cover - unused
        raise AssertionError("controller recovery called a model through the port")
        yield


class LoopSource(TurnResponderSource):
    """Script by projected position; fresh asks are counted and asserted."""

    def __init__(self) -> None:
        self.asks: list[str] = []

    def frozen_refs(self) -> dict[str, Any]:
        return {"config": "loop-v1", "task": SQL_TASK.id}

    def responder_for(self, messages, input_refs) -> DecisionResponder:
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        tail = str(tool_messages[-1]["content"]) if tool_messages else ""
        count = len(tool_messages)
        if _MODE == "crash-after-feedback" and count == 1:
            # The previous action is complete and its governed feedback has
            # been rebuilt, but no next decision has been accepted yet.
            os._exit(9)
        source = self

        class _One(DecisionResponder):
            async def respond(self, run_ref, decision_step_id, refs) -> DecisionProposal:
                source.asks.append(decision_step_id)
                if _MODE == "wait" and count == 1:
                    return DecisionProposal(
                        accepted_output={"text": "working"},
                        actions=(AcceptedAction("loop-a2", "fetch", {"action_key": "loop-a2", "business": "fetch-1"}),),
                    )
                if count == 0:
                    return DecisionProposal(
                        accepted_output={"text": "working"},
                        actions=(
                            AcceptedAction("loop-a1", "search", {"action_key": "loop-a1", "business": "search-1"}),
                        ),
                    )
                if count == 1 and tail in (_RESULT_FIRST, _RESULT_FIRST_REPR):
                    return DecisionProposal(
                        accepted_output={"text": "working"},
                        actions=(AcceptedAction("loop-a2", "fetch", {"action_key": "loop-a2", "business": "fetch-1"}),),
                    )
                if count >= 2 and (tail in (_RESULT_SECOND, _RESULT_SECOND_REPR) or _RECOVERING):
                    # After a cross-process replay the projected tails may
                    # mix serializations; any second result means turn 2
                    # completed and only the final answer remains.
                    return DecisionProposal(
                        accepted_output={"text": "the loop answer"},
                        actions=(),
                        kind=KIND_FINAL_ANSWER,
                    )
                raise AssertionError(f"scripted loop asked for an unsolicited turn at ({count}, {tail!r})")

        return _One()


class CrashAfterDecisionCommitStore(SqlAcceptedDecisionStore):
    """Hard-exit once turn 2's decision is durably committed."""

    def __init__(self, backend, target_step: str) -> None:
        super().__init__(backend)
        self._target = target_step

    async def commit(self, record, ownership=None):
        committed = await super().commit(record, ownership)
        if committed.decision_step_id == self._target:
            os._exit(9)
        return committed


class WaitPolicy:
    """The fetch tool requires approval in the wait/wake modes."""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, tool_meta, rules, eval_context, arguments=None):
        if _WAITING and tool_meta["name"] == "fetch":
            return AccessDecision.REQUIRE_APPROVAL
        return AccessDecision.EXECUTE


class WaitApproval:
    """Raise the durable wait on the first ask; approve afterwards."""

    def __init__(self) -> None:
        self.calls = 0

    async def request_approval(self, tool_name, arguments, risk_level, context):
        self.calls += 1
        if _MODE == "wait":
            raise ApprovalWaitSignal(tool_name, arguments)
        return ApprovalDecision(approved=True, reason="wake grant", approval_ref="approval-loop-1")


def _executor(backend, owner, port):
    hook = SqlActionLedgerHook(
        backend,
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    if _WAITING:
        return ToolCallExecutor(
            port=port,
            event_store=InMemoryEventStore(),
            action_hook=hook,
            access_policy=WaitPolicy(),
            approval_callback=WaitApproval(),
        )
    return ToolCallExecutor(port=port, event_store=InMemoryEventStore(), action_hook=hook)


async def main() -> None:
    data = json.loads(sys.argv[1])
    backend = SqlDurableStore(data["url"], lease_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    owner = DecisionOwnership(**data["ownership"])
    store: SqlAcceptedDecisionStore = (
        CrashAfterDecisionCommitStore(backend, "agent-turn-000002")
        if _MODE == "crash-after-decision"
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
    if _MODE == "crash-after-action":
        # Crash after the action's effect AND its durable outcome: a true
        # worker death after one completed action, with the batch unfinished.
        record_outcome = backend.record_outcome_ex

        def exit_after_first_outcome(*args, **kwargs):
            record_outcome(*args, **kwargs)
            os._exit(9)

        backend.record_outcome_ex = exit_after_first_outcome
    port = BusinessPort()
    source = LoopSource()
    base_messages = [{"role": "user", "content": "run the loop"}]
    initial_refs = {**source.frozen_refs(), "controller": "agent-loop/1", "turn": 1}
    controller = AgentController(
        AgentTurn(store, source.responder_for(base_messages, initial_refs), _executor(backend, owner, port)),
        source,
    )
    spec = ControllerRunSpec(
        run_ref=RUN,
        base_messages=base_messages,
        ownership=owner,
        limits=ControllerLimits(max_turns=8),
    )
    if _MODE == "wait":
        try:
            await controller.run(spec)
        except ApprovalWaitSignal:
            print(json.dumps({"parked": True, "asks": source.asks}), flush=True)
            os._exit(0)
        raise AssertionError("wait mode must park, not finish")
    if _MODE in {"receipt-loss", "recover-unknown"}:
        outcome = await controller.run(spec)
        print(
            json.dumps(
                {
                    "state": outcome.state,
                    "reason": outcome.reason,
                    "turns": outcome.turns,
                    "asks": source.asks,
                    "decision_step_ids": list(outcome.decision_step_ids),
                }
            ),
            flush=True,
        )
        os._exit(0)
    outcome = await controller.run(spec)
    print(
        json.dumps(
            {
                "state": outcome.state,
                "reason": outcome.reason,
                "final_text": outcome.final_text,
                "turns": outcome.turns,
                "asks": source.asks,
                "decision_step_ids": list(outcome.decision_step_ids),
                "effects": port.calls,
            }
        ),
        flush=True,
    )
    # Do not allow normal Python teardown to carry process-local state forward.
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
