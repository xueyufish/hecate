"""Hard-exit probe for acceptance corrections over the real SQL execution stack."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime

from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.accepted_decision import AcceptedAction, DecisionOwnership, RunRef
from hecate_runtime.agent_controller import (
    AgentController,
    ControllerContinuation,
    ControllerContinuationPolicy,
    ControllerRunSpec,
    TurnResponderSource,
)
from hecate_runtime.agent_turn import AgentTurn
from hecate_runtime.decision_step import KIND_FINAL_ANSWER, DecisionProposal, DecisionResponder
from hecate_runtime.delivery_acceptance import continuation_feedback_payload, declared_acceptance, evaluate_delivery
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor

RUN = RunRef("domain", "run-loop-1")
TASK = BackendRef(RefKind.TASK, RUN.issuer_domain, "task-run-loop-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
MODE = sys.argv[2]
EFFECT = sys.argv[3]


class ProbeStore(SqlAcceptedDecisionStore):
    """Crash after the selected decision has committed, before dispatch."""

    async def commit(self, record, ownership=None):
        committed = await super().commit(record, ownership)
        target = "agent-turn-000002" if MODE == "candidate" else "agent-turn-000003"
        if MODE in {"candidate", "next-decision"} and committed.decision_step_id == target:
            os._exit(9)
        return committed


class ProbePort(StubRuntimePort):
    """Append effects durably so a second process detects duplicate dispatch."""

    async def tool_execute(self, name, args, context=None):
        with open(EFFECT, "a", encoding="utf-8") as stream:
            stream.write(args["key"] + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"key": args["key"]}


class ProbeSource(TurnResponderSource):
    """Ask by durable turn and require feedback before corrective actions."""

    def __init__(self):
        self.asks = []

    def frozen_refs(self):
        return {"probe": "continuation/1"}

    def responder_for(self, messages, input_refs):
        source = self
        turn = input_refs["turn"]

        class Responder(DecisionResponder):
            async def respond(self, run_ref, decision_step_id, refs):
                source.asks.append(turn)
                if turn in (3, 4):
                    assert any("continuation_feedback" in str(m) for m in messages)
                if turn == 1:
                    actions = (AcceptedAction("before", "write", {"key": "before"}),)
                elif turn == 2:
                    return DecisionProposal({"text": "draft"}, (), kind=KIND_FINAL_ANSWER)
                elif turn == 3:
                    actions = (
                        AcceptedAction("correction-a", "write", {"key": "correction-a"}),
                        AcceptedAction("correction-b", "write", {"key": "correction-b"}),
                    )
                elif turn == 4:
                    return DecisionProposal({"text": "verified"}, (), kind=KIND_FINAL_ANSWER)
                else:
                    raise AssertionError(f"unexpected turn {turn}")
                return DecisionProposal({"text": "working"}, actions)

        return Responder()


async def main():
    config = json.loads(sys.argv[1])
    backend = SqlDurableStore(config["url"], lease_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    owner = DecisionOwnership(**config["ownership"])
    lease = LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, "")
    state = backend.get_task_state(TASK)
    if state.lifecycle_state is TaskLifecycleState.QUEUED:
        backend.apply_task_state(TASK, TaskLifecycleState.RUNNING, lease=lease)
    if MODE == "partial-action":
        original = backend.record_outcome_ex
        writes = 0

        def record_then_exit(outcome, **kwargs):
            nonlocal writes
            original(outcome, **kwargs)
            writes += 1
            if writes == 2:
                os._exit(9)

        backend.record_outcome_ex = record_then_exit
    store = ProbeStore(backend)
    source = ProbeSource()
    executor = ToolCallExecutor(
        port=ProbePort(),
        action_hook=SqlActionLedgerHook(backend, task_ref=TASK, run_ref=SQL_RUN, holder=owner.holder, lease=lease),
    )
    controller = AgentController(AgentTurn(store, source.responder_for([], {"turn": 1}), executor), source)
    acceptance = declared_acceptance(
        {"version": 1, "required_checks": [{"kind": "content_contains", "value": "verified"}]}
    )
    policy = ControllerContinuationPolicy(max_continuations=1)
    block = (backend.get_task_state(TASK).extra or {}).get("continuation")
    entries = ControllerContinuation.from_block(
        block,
        policy,
        acceptance,
        task_ref={"issuer": TASK.issuer_domain, "id": TASK.id},
        run_ref={"issuer": RUN.issuer_domain, "id": RUN.run_id},
    )
    spec = ControllerRunSpec(
        run_ref=RUN,
        base_messages=[{"role": "user", "content": "finish"}],
        ownership=owner,
        acceptance=acceptance,
        continuation_policy=policy,
        continuations=entries,
    )
    while True:
        outcome = await controller.run(spec)
        assert outcome.is_final_answer
        receipt = evaluate_delivery(
            outcome.final_text,
            acceptance,
            final_decision={
                "decision_step_id": outcome.final_decision_step_id,
                "content_digest": outcome.final_decision_digest,
            },
            task_ref={"issuer": TASK.issuer_domain, "id": TASK.id},
            run_ref={"issuer": RUN.issuer_domain, "id": RUN.run_id},
        )
        if receipt["status"] == "passed":
            print(json.dumps({"asks": source.asks, "text": outcome.final_text}), flush=True)
            return
        assert not spec.continuations
        attempt = {
            "attempt": 1,
            "candidate_decision_step_id": outcome.final_decision_step_id,
            "candidate_digest": outcome.final_decision_digest,
            "effective_turn": outcome.turns + 1,
            "standard_version": receipt["version"],
            "standard_sha256": receipt["declaration_sha256"],
            "status": receipt["status"],
            "policy_sha256": policy.sha256,
            "acceptance_record": receipt,
            "feedback": continuation_feedback_payload(receipt, attempt=1, max_continuations=1),
        }
        backend.record_continuation_trigger(
            TASK,
            attempt,
            policy_sha256=policy.sha256,
            max_continuations=1,
            lease=lease,
        )
        if MODE == "trigger":
            os._exit(9)
        spec = replace(spec, continuations=(ControllerContinuation.from_record(attempt),))


if __name__ == "__main__":
    asyncio.run(main())
