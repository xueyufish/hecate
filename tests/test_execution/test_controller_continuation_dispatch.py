"""Bounded controlled continuation after declarative acceptance failure.

Covers the platform slice of the continuation closure: an explicitly
declared ``controller.continuation`` policy lets a delivery gate that
failed deterministic checks persist one trigger fact per rejected
candidate and re-enter the SAME task/run decision chain with an internal
feedback message — the rejected answer and its accepted decision stay
immutable, no human input revision is created, and the platform run is
never re-minted. Sustained failure stops at the declared bound with the
real ``acceptance_failed`` classification, unverified checks never
continue, a config without a declared acceptance standard is rejected at
submission, crash redelivery consumes a trigger exactly once, and the
observation surface is read-only.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.execution.task_control import task_ref_of
from tests.test_execution.test_controller_loop_dispatch import (  # noqa: F401 - fixture registration
    OUTPUT_SCHEMA,
    OWNER,
    WS,
    ScriptedLoopPort,
    _dispatch_to_completion,
    harness,
)

_DECLARED = {
    "version": 2,
    "required_checks": [{"kind": "content_contains", "value": "FINAL"}],
}
_POLICY = {"version": 1, "max_continuations": 1}


class _ContinuationPort(ScriptedLoopPort):
    """First delivery fails the declared check; post-feedback delivery passes.

    The corrected text is served only after a persisted continuation
    feedback message reached the model input, so the test fails if the
    loop continued for any other reason.
    """

    def __init__(self, *, first_text: str, final_text: str) -> None:
        super().__init__(final_text=first_text)
        self._corrected_text = final_text
        self.feedback_payloads: list[dict[str, Any]] = []

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and "continuation_feedback" in content:
                self.feedback_payloads.append(json.loads(content)["continuation_feedback"])
        self.model_calls += 1
        self.messages = messages
        self.model_config = config
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if not tool_messages:
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": json.dumps(self.tool_arguments)},
                    }
                ],
            }
            return
        if self.feedback_payloads:
            yield {"content": self._corrected_text, "tool_calls": None}
        else:
            yield {"content": self.final_text, "tool_calls": None}


async def test_acceptance_failure_continues_the_same_chain_and_passes(harness) -> None:  # noqa: F811
    """fail → persisted trigger → corrected delivery → succeeded, one run."""
    port = _ContinuationPort(first_text="a draft without the marker", final_text="now FINAL")
    record, t_ref, port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 6, "continuation": _POLICY}
    )
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == "now FINAL"
    assert result["acceptance"]["status"] == "passed"
    # The rejected candidate and the corrected delivery are separate
    # committed decisions in the same chain: exactly one fresh ask after
    # the feedback (turn 3), never a re-ask of committed turns.
    assert port.model_calls == 3
    # The feedback is the persisted internal control message — the exact
    # failed check, the candidate identity and the bound, and never a
    # human input envelope.
    assert len(port.feedback_payloads) == 1
    feedback = port.feedback_payloads[0]
    assert feedback["attempt"] == 1 and feedback["max_continuations"] == 1
    assert feedback["candidate_decision_step_id"] == "agent-turn-000002"
    assert feedback["standard"] == {"version": 2, "declaration_sha256": feedback["standard"]["declaration_sha256"]}
    assert feedback["failed_checks"] == [
        {"kind": "content_contains", "status": "failed", "detail": feedback["failed_checks"][0]["detail"]}
    ]
    assert "acceptance feedback" in feedback["instruction"]
    # The durable trigger fact: one consumed attempt pinned to the candidate.
    state = harness["store"].get_task_state(t_ref)
    block = state.extra["continuation"]
    assert block["max_continuations"] == 1
    assert [item["attempt"] for item in block["attempts"]] == [1]
    # The trigger pins the committed DECISION digest; the feedback names
    # the rejected TEXT digest — two deliberate digest families.
    assert len(block["attempts"][0]["candidate_digest"]) == 64
    assert block["attempts"][0]["candidate_digest"] != feedback["candidate_content_sha256"]
    import hashlib as _hashlib

    assert feedback["candidate_content_sha256"] == _hashlib.sha256(b"a draft without the marker").hexdigest()
    assert block["attempts"][0]["effective_turn"] == 3
    # The governance fact reached the durable event log for the run.
    events = harness["store"].read_events(harness["store"].run_for_task(t_ref), limit=100).events
    assert sum(event.payload.get("event_type") == "task_continuation" for event in events) == 1


async def test_sustained_failure_stops_at_the_declared_bound(harness) -> None:  # noqa: F811
    """The bound ends the corrections; the task keeps acceptance_failed."""
    port = _ContinuationPort(first_text="a draft without the marker", final_text="another draft, still missing")
    record, t_ref, port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 6, "continuation": _POLICY}
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result["error_category"] == "acceptance_failed"
    assert result["content"] == "another draft, still missing"
    # Two deliveries asked; the second candidate never consumed a trigger.
    assert port.model_calls == 3
    state = harness["store"].get_task_state(t_ref)
    assert len(state.extra["continuation"]["attempts"]) == 1


async def test_unverified_checks_never_consume_a_trigger(harness, monkeypatch) -> None:  # noqa: F811
    """A checker outage keeps the delivery unverified and the chain closed."""
    import hecate_runtime.delivery_acceptance as acceptance_module

    def unavailable(*args):
        raise RuntimeError("checker unavailable after admission")

    class CheckerOutagePort(_ContinuationPort):
        async def llm_invoke_structured(self, messages, config):
            async for answer in super().llm_invoke_structured(messages, config):
                if answer.get("content"):
                    monkeypatch.setattr(acceptance_module, "_validate_check_strict", unavailable)
                yield answer

    port = CheckerOutagePort(first_text="losing the checker", final_text="unused")
    record, t_ref, _port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 6, "continuation": _POLICY}
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result["error_category"] == "acceptance_unverified"
    assert port.model_calls == 2  # the loop never re-entered
    state = harness["store"].get_task_state(t_ref)
    assert (state.extra or {}).get("continuation") is None


async def test_continuation_without_acceptance_declaration_is_rejected(harness) -> None:  # noqa: F811
    """Corrections without a frozen standard are refused before any task exists."""
    from hecate.execution.task_control import TaskControlValidationError

    with pytest.raises(TaskControlValidationError):
        await _dispatch_to_completion(
            harness,
            port=ScriptedLoopPort(final_text="never"),
            controller={"max_turns": 4, "continuation": _POLICY},
        )
    assert harness["store"].list_tasks() == []


async def test_malformed_continuation_bound_is_rejected(harness) -> None:  # noqa: F811
    from hecate.execution.task_control import TaskControlValidationError

    for bad in ({"max_continuations": True}, {"max_continuations": 0}, {"bound": 1}, {"max_continuations": 1, "x": 2}):
        with pytest.raises(TaskControlValidationError):
            await _dispatch_to_completion(
                harness,
                port=ScriptedLoopPort(final_text="never"),
                acceptance=_DECLARED,
                controller={"max_turns": 4, "continuation": bad},
            )
    assert harness["store"].list_tasks() == []


@pytest.mark.parametrize("corruption", [None, "feedback", "checker", "standard", "attempt"])
async def test_trigger_persist_crash_recovers_without_double_consume(
    harness,  # noqa: F811
    monkeypatch,
    corruption,
) -> None:
    """A crash between the trigger commit and the next model call resumes once.

    The dispatch dies after the trigger fact is durable but before the
    corrected turn runs. The redelivery rebuilds the continuation position
    from the stored attempts, replays committed turns with zero model
    calls and finishes — the candidate is never recorded twice.
    """
    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_control import TaskControlService
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    port = _ContinuationPort(first_text="a draft without the marker", final_text="now FINAL")
    from tests.test_execution.test_controller_loop_dispatch import _ScriptedAssembly

    assembly = _ScriptedAssembly(port)
    assembly.patch()
    harness["cleanups"].append(assembly.unpatch)
    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    worker = DurableWorker(
        harness["store"], dispatcher, leases=harness["store"].leases, max_attempts=6, backoff_base=0.05
    )
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=worker,
        )
        result = await service.submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="continuation crash recovery",
            agent_id=harness["agent_id"],
            input={
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 6, "continuation": _POLICY},
                "acceptance": _DECLARED,
            },
        )
    t_ref = task_ref_of(uuid.UUID(result.task_ref.id))

    # Crash the dispatcher exactly when the trigger has just been persisted.
    original = harness["store"].record_continuation_trigger
    persisted = {"n": 0}

    def crash_after_first(*args, **kwargs):
        outcome = original(*args, **kwargs)
        persisted["n"] += 1
        if persisted["n"] == 1:
            if corruption:
                block = json.loads(json.dumps(outcome.extra["continuation"]))
                item = block["attempts"][0]
                if corruption == "feedback":
                    item["feedback"]["instruction"] = "rewritten"
                elif corruption == "checker":
                    item["acceptance_record"]["checker_version"] = "unavailable/99"
                elif corruption == "standard":
                    item["standard_sha256"] = "f" * 64
                else:
                    item["attempt"] = 2
                harness["store"].apply_task_state(
                    t_ref, TaskLifecycleState.RUNNING, extra_update={"continuation": block}
                )
            raise RuntimeError("dispatch died after the trigger commit (simulated crash)")
        return outcome

    monkeypatch.setattr(harness["store"], "record_continuation_trigger", crash_after_first)
    from tests.test_execution.test_controller_loop_dispatch import _dispatch_to_state

    record = await _dispatch_to_state(harness, t_ref, worker, "reconciliation_required" if corruption else "succeeded")
    if corruption:
        assert port.model_calls == 2
        assert record.extra["result"]["error_category"] == "continuation_history_unavailable"
        return
    # The recovery consumed the stored trigger: no second persist ever ran.
    assert persisted["n"] == 1
    state = harness["store"].get_task_state(t_ref)
    assert [item["attempt"] for item in state.extra["continuation"]["attempts"]] == [1]
    assert port.model_calls == 3
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == "now FINAL"


async def test_observation_summary_is_read_only_and_workspace_scoped(harness) -> None:  # noqa: F811
    """GET exposes the minimal continuation trail; no state moves on read."""
    port = _ContinuationPort(first_text="a draft without the marker", final_text="now FINAL")
    record, t_ref, _port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 6, "continuation": _POLICY}
    )
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    revision_before = record.revision

    from hecate.execution.task_control import TaskControlService

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )
        detail = await service.get_task_detail(WS, uuid.UUID(t_ref.id))
        # A foreign workspace cannot observe the task at all.
        from hecate.execution.task_control import TaskControlNotFoundError

        with pytest.raises(TaskControlNotFoundError):
            await service.get_task_detail(uuid.uuid4(), uuid.UUID(t_ref.id))
    summary = detail["continuation"]
    assert summary["max_continuations"] == 1
    assert [item["attempt"] for item in summary["attempts"]] == [1]
    assert summary["attempts"][0]["candidate_decision_step_id"] == "agent-turn-000002"
    assert summary["attempts"][0]["status"] == "acceptance_failed"
    assert summary["attempts"][0]["effective_turn"] == 3
    assert summary["attempts"][0]["failed_checks"][0]["kind"] == "content_contains"
    after = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert after.revision == revision_before  # the read moved nothing


async def test_last_turn_failure_retains_delivery_without_consuming_trigger(harness) -> None:  # noqa: F811
    port = _ContinuationPort(first_text="last turn draft", final_text="unused FINAL")
    record, _t_ref, _port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 2, "continuation": _POLICY}
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["result"]["content"] == "last turn draft"
    assert record.extra["result"]["error_category"] == "acceptance_failed"
    assert "continuation" not in record.extra
    assert port.model_calls == 2


async def test_corrective_action_turn_limit_preserves_rejected_delivery(harness) -> None:  # noqa: F811
    class CorrectionActionPort(_ContinuationPort):
        async def llm_invoke_structured(self, messages, config):
            async for chunk in super().llm_invoke_structured(messages, config):
                if self.feedback_payloads:
                    chunk = {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "correction-read",
                                "type": "function",
                                "function": {"name": "query_inventory", "arguments": json.dumps({"warehouse": "new"})},
                            }
                        ],
                    }
                yield chunk

    port = CorrectionActionPort(first_text="retained draft", final_text="unused")
    record, _t_ref, _port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 3, "continuation": _POLICY}
    )
    result = record.extra["result"]
    assert result["content"] == "retained draft"
    assert result["acceptance"]["status"] == "acceptance_failed"
    assert result["error_category"] == "controller_turn_limit"
    assert port.model_calls == 3


async def test_input_wait_after_trigger_resumes_same_correction(harness) -> None:  # noqa: F811
    from tests.test_execution.test_controller_loop_dispatch import (
        _dispatch_to_state,
        _issue_input_wake,
        _submit_without_dispatch,
        _worker_for,
    )

    class CorrectionInputPort(_ContinuationPort):
        async def llm_invoke_structured(self, messages, config):
            provided = any("provided_input" in str(message.get("content")) for message in messages)
            async for chunk in super().llm_invoke_structured(messages, config):
                if self.feedback_payloads and not provided:
                    chunk = {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "correction-input",
                                "type": "function",
                                "function": {"name": "query_inventory", "arguments": json.dumps({"need_region": True})},
                            }
                        ],
                    }
                yield chunk

        async def tool_execute(self, name, args, context=None):
            if args.get("need_region"):
                self.tool_calls.append((name, dict(args)))
                return {"status": "input_required", "contract": {"fields": ["region"]}}
            return await super().tool_execute(name, args, context)

    port = CorrectionInputPort(first_text="draft", final_text="now FINAL")
    t_ref, task_uuid = await _submit_without_dispatch(
        harness, port, acceptance=_DECLARED, controller={"max_turns": 6, "continuation": _POLICY}
    )
    worker = _worker_for(harness)
    parked = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
    assert port.model_calls == 3
    assert len(parked.extra["continuation"]["attempts"]) == 1
    issued = await _issue_input_wake(
        harness, task_uuid, parked.extra["wait"]["wait_token"], {"region": "EU"}, worker=worker
    )
    assert issued.record.state.value == "applied"
    final = await _dispatch_to_state(harness, t_ref, worker, "succeeded")
    assert final.extra["result"]["content"] == "now FINAL"
    assert port.model_calls == 4
    assert len(port.tool_calls) == 2
    assert len(final.extra["continuation"]["attempts"]) == 1
    revisions = harness["store"].list_input_revisions(t_ref)
    assert len(revisions) == 1 and revisions[0]["effective_turn"] == 4


async def test_cancel_after_trigger_prevents_the_corrective_model_call(harness, monkeypatch) -> None:  # noqa: F811
    original = harness["store"].record_continuation_trigger

    def cancel_after_commit(task_ref, *args, **kwargs):
        recorded = original(task_ref, *args, **kwargs)
        harness["store"].apply_task_state(task_ref, TaskLifecycleState.CANCELLED)
        return recorded

    monkeypatch.setattr(harness["store"], "record_continuation_trigger", cancel_after_commit)
    port = _ContinuationPort(first_text="draft", final_text="unused FINAL")
    final, _t_ref, _port = await _dispatch_to_completion(
        harness, port=port, acceptance=_DECLARED, controller={"max_turns": 6, "continuation": _POLICY}
    )
    assert final.lifecycle_state is TaskLifecycleState.CANCELLED
    assert port.model_calls == 2
