"""Query receipts from real Controller decisions and action dispatch."""

from __future__ import annotations

import uuid

from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.task_control import TaskControlService
from tests.test_execution import test_controller_loop_dispatch as controller_tests

harness = controller_tests.harness


async def test_dispatched_controller_action_has_a_verified_read_only_receipt(harness):
    """Real responder, acceptance and ledger paths produce resolvable evidence."""
    port = controller_tests.ScriptedLoopPort(final_text='{"answer":"three items in stock"}')
    record, task_ref, _ = await controller_tests._dispatch_to_completion(
        harness, port=port, output_schema=controller_tests.OUTPUT_SCHEMA
    )
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db, store=harness["store"], recorder=harness["store"], backend="postgres", ledger_source="core"
        )
        detail = await service.get_task_detail(controller_tests.WS, uuid.UUID(task_ref.id))
        run_id = uuid.UUID(detail["runs"][0]["run_id"])
        actions = await service.list_run_actions(controller_tests.WS, run_id)
        key = actions["actions"][0]["action_key"]
        run_ref = BackendRef(RefKind.RUN, "hecate", str(run_id))
        before = harness["store"].list_run_actions(run_ref)
        events_before = harness["store"].read_events(run_ref)
        for _ in range(2):
            receipt = (await service.get_run_action_evidence(controller_tests.WS, run_id, key))["receipt"]
            assert receipt["tool_name"] == "query_inventory"
            assert receipt["decision_ref"]["status"] == "linked"
            assert receipt["decision_ref"]["decision_step_id"] == "agent-turn-000001"
            assert receipt["arguments_verification"]["status"] == "verified"
            assert receipt["result_verification"]["status"] == "verified"
        assert harness["store"].list_run_actions(run_ref) == before
        assert harness["store"].read_events(run_ref) == events_before
        assert harness["store"].get_task_state(task_ref) == record
    assert port.model_calls == 2
    assert len(port.tool_calls) == 1
