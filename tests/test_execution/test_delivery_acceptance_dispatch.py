"""Shared versioned delivery acceptance on the platform dispatch path.

Covers the platform slice of the shared acceptance closure: the durable
record binds the standard (version + digest), the final decision's
identity and digest, the full task/run namespace, the checker version and
the trusted platform evaluator; platform and Runner verdicts coincide for
one declaration and one delivery (schema passes, required content check
fails); a terminal-commit crash after the final decision recovers by
replay without re-asking the model or re-executing actions; and a standard
rewritten under the same version number in durable storage stops the task
for reconciliation instead of re-evaluating committed decisions.
"""

from __future__ import annotations

import json
import uuid

import pytest

from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.execution.task_control import task_ref_of
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from tests.test_execution.test_controller_loop_dispatch import (  # noqa: F401 - fixture registration
    OUTPUT_SCHEMA,
    OWNER,
    WS,
    ScriptedLoopPort,
    _dispatch_to_completion,
    _dispatch_to_terminal,
    _submit_without_dispatch,
    harness,
)

_DECLARED = {
    "version": 3,
    "required_checks": [
        {"kind": "output_schema", "schema": OUTPUT_SCHEMA},
        {"kind": "content_contains", "value": "three items"},
    ],
}


async def test_acceptance_record_binds_standard_decision_namespace_and_checker(
    harness,  # noqa: F811
) -> None:
    """The durable record binds the standard, the decision and the evaluator."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    acceptance = (record.extra or {})["result"]["acceptance"]
    assert acceptance["checker_version"] == "delivery-acceptance/1"
    assert len(acceptance["declaration_sha256"]) == 64
    # The final decision's durable identity and digest, not a content re-read.
    assert acceptance["final_decision"]["decision_step_id"] == "agent-turn-000002"
    assert len(acceptance["final_decision"]["content_digest"]) == 64
    # Full namespace of the task and the controller run that decided.
    assert acceptance["task_ref"] == {"issuer": t_ref.issuer_domain, "id": t_ref.id}
    assert acceptance["run_ref"]["issuer"] == t_ref.issuer_domain
    # Responsibility identity from the trusted server-side context.
    assert acceptance["evaluated_by"] == {"host": "platform", "workspace_id": str(WS), "submitted_by": str(OWNER)}


async def test_hosts_evaluate_one_delivery_identically(
    harness,  # noqa: F811
) -> None:
    """Platform and Runner verdicts coincide — schema pass, content check fail."""
    from hecate_runtime.delivery_acceptance import evaluate_delivery, evaluation_declaration

    from hecate.execution.task_dispatcher import _DispatchContext

    text = json.dumps({"answer": "nothing relevant"})  # schema passes, content fails
    context = _DispatchContext(
        task_id=uuid.uuid4(),
        workspace_id=WS,
        agent_id=uuid.uuid4(),
        user_id=OWNER,
        goal="parity",
        messages=[],
        model=None,
        stream=False,
        provided=None,
        acceptance=_DECLARED,
    )

    class _Run:
        issuer_domain = "hecate.platform"
        run_id = "run-parity"

    platform_outcome = PlatformTaskDispatcher._evaluate_delivery(
        text,
        context,
        task_ref=task_ref_of(uuid.uuid4()),
        run_ref=_Run(),
        final_decision_step_id="agent-turn-000002",
        final_decision_digest="digest-parity",
    )
    runner_record = evaluate_delivery(  # the Runner host's call shape
        text,
        evaluation_declaration(_DECLARED, None),
        final_decision={"decision_step_id": "agent-turn-000002", "content_digest": "digest-parity"},
        evaluator={"host": "runner", "principal": "app-reader"},
    )
    platform_record = platform_outcome["acceptance"]
    # Everything verdict-bearing is identical across the two hosts.
    for key in (
        "checker_version",
        "version",
        "declaration_sha256",
        "status",
        "checks",
        "content_sha256",
        "final_decision",
    ):
        assert platform_record[key] == runner_record[key], key
    assert platform_record["status"] == "acceptance_failed"
    assert platform_outcome["error_category"] == "acceptance_failed"


async def test_crash_after_final_decision_before_terminal_recovers_without_reasking(
    harness,  # noqa: F811
    monkeypatch,
) -> None:
    """Terminal-commit crash after the final decision: replay, never re-ask.

    The final answer decision is durably committed; the terminal state
    commit dies. A redelivery replays the chain with zero model calls and
    zero re-executed actions, re-derives the acceptance evaluation under
    the same frozen declaration and commits the terminal state once.
    """
    from hecate_durable.worker import DurableWorker

    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    t_ref, _task_uuid = await _submit_without_dispatch(
        harness,
        port,
        acceptance={"version": 2, "required_checks": [{"kind": "content_contains", "value": "three items"}]},
    )
    original_finish = PlatformTaskDispatcher._finish
    finish_calls = {"n": 0}

    async def crash_first_finish(self, *args, **kwargs):  # noqa: ANN001, ANN003
        finish_calls["n"] += 1
        if finish_calls["n"] == 1:
            raise RuntimeError("terminal commit lost (simulated crash)")
        return await original_finish(self, *args, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_finish", crash_first_finish)
    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    worker = DurableWorker(
        harness["store"], dispatcher, leases=harness["store"].leases, max_attempts=5, backoff_base=0.05
    )
    record = await _dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert finish_calls["n"] == 2
    # Zero model re-asks and zero re-executed actions across the recovery.
    assert port.model_calls == 2
    assert port.tool_calls == [("query_inventory", {"sku": "MODEL-CHOSEN"})]
    acceptance = (record.extra or {})["result"]["acceptance"]
    assert acceptance["status"] == "passed" and acceptance["version"] == 2
    assert acceptance["final_decision"]["decision_step_id"] == "agent-turn-000002"
    # One terminal governance event — recovery never duplicates the fact.
    run_events = harness["store"].read_events(harness["store"].run_for_task(t_ref), limit=100).events
    terminals = [e for e in run_events if e.payload.get("event_type") == "run_terminal"]
    assert len(terminals) == 1
    assert terminals[0].payload["acceptance"] == acceptance


async def test_same_version_standard_rewrite_stops_for_reconciliation(
    harness,  # noqa: F811
) -> None:
    """A rewritten standard (same version) can never re-evaluate old decisions.

    The decisions — including the final answer — commit under version 2 of
    the standard; the terminal write dies; the durable task input's
    declaration is then rewritten in storage under the SAME version with a
    different check parameter. The redelivery must conflict on the pinned
    declaration digest and park the task for reconciliation, never sign an
    acceptance and never re-ask the model.
    """
    from hecate_durable.storage.models import TaskStateRow
    from hecate_durable.worker import DurableWorker

    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    t_ref, _task_uuid = await _submit_without_dispatch(
        harness,
        port,
        acceptance={"version": 2, "required_checks": [{"kind": "content_contains", "value": "three items"}]},
    )

    async def never_finish(self, *args, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("terminal commit lost (simulated crash)")

    finish_patch = pytest.MonkeyPatch()
    finish_patch.setattr(PlatformTaskDispatcher, "_finish", never_finish)
    try:
        dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
        worker = DurableWorker(
            harness["store"], dispatcher, leases=harness["store"].leases, max_attempts=2, backoff_base=0.05
        )
        # First dispatch: both decisions (including the final answer) commit;
        # the terminal write dies and the bounded attempts park the task.
        parked = await _dispatch_to_terminal(harness, t_ref, worker=worker)
        assert parked.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
        assert port.model_calls == 2
    finally:
        finish_patch.undo()

    # The tamper: same version, different check parameter, written directly
    # into the durable task input the redelivery re-reads.
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        payload = dict(row.input_payload or {})
        payload["acceptance"] = {
            "version": 2,
            "required_checks": [{"kind": "content_contains", "value": "TAMPERED"}],
        }
        row.input_payload = payload
        row.lifecycle_state = "queued"
        row.revision = row.revision + 1
        row.extra = {**(row.extra or {}), "dispatch_attempts": 0, "dispatch_starts": 0}
        session.commit()

    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    worker = DurableWorker(
        harness["store"], dispatcher, leases=harness["store"].leases, max_attempts=2, backoff_base=0.05
    )
    record = await _dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    result = (record.extra or {}).get("result") or {}
    assert "acceptance" not in result  # no acceptance was ever signed
    assert result["error_category"] == "decision_history_unavailable"
    assert record.extra["dispatch_starts"] == 1
    assert port.model_calls == 2  # the chain never re-asked the model


async def test_checker_outage_keeps_platform_delivery_unverified(
    harness,  # noqa: F811
    monkeypatch,
) -> None:
    """A required checker outage after admission keeps content without granting success."""
    import hecate_runtime.delivery_acceptance as acceptance_module

    def unavailable(*args):
        raise RuntimeError("checker unavailable after admission")

    class CheckerOutagePort(ScriptedLoopPort):
        """Lose the checker only after the model's final output has arrived."""

        async def llm_invoke_structured(self, *args, **kwargs):
            """Forward the scripted result and inject an evaluation-time outage."""
            async for answer in super().llm_invoke_structured(*args, **kwargs):
                if answer.get("content"):
                    monkeypatch.setattr(acceptance_module, "_validate_check_strict", unavailable)
                yield answer

    port = CheckerOutagePort(final_text="verified")
    record, _task_ref, _port = await _dispatch_to_completion(
        harness,
        port=port,
        acceptance={"required_checks": [{"kind": "content_contains", "value": "verified"}]},
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["result"]["content"] == "verified"
    assert record.extra["result"]["acceptance"]["status"] == "acceptance_unverified"
    assert port.model_calls == 2
