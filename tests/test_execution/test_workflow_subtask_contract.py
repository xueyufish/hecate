"""Workflow agent-subtask contract tests (step5e tasks 5.1-5.4).

Covers: extended step schema (backward compat + explicit rejection),
artifact/output validation gating the parent's advance, failure-source
distinction, and callback idempotency through the existing verified chain.
"""

from __future__ import annotations

import pytest

from hecate.execution.workflow_child import WorkflowChildTaskAdapter


class TestPlanSchemaExtension:
    def test_goal_only_still_accepted(self) -> None:
        plan = WorkflowChildTaskAdapter.plan({"workflow": {"steps": [{"goal": "do a thing"}], "on_failure": "fail"}})
        assert plan is not None
        assert plan.steps[0]["goal"] == "do a thing"

    def test_extended_fields_accepted(self) -> None:
        plan = WorkflowChildTaskAdapter.plan(
            {
                "workflow": {
                    "steps": [
                        {
                            "goal": "produce report",
                            "mode": "chat",
                            "output_schema": {
                                "type": "object",
                                "required": ["report"],
                                "properties": {"report": {"type": "string"}},
                            },
                            "artifact": "report",
                        }
                    ],
                    "on_failure": "await",
                }
            }
        )
        assert plan.steps[0]["artifact"] == "report"
        assert plan.on_failure == "await"

    def test_invalid_mode_rejected_locatably(self) -> None:
        with pytest.raises(ValueError, match=r"steps\[0\]\.mode"):
            WorkflowChildTaskAdapter.plan({"workflow": {"steps": [{"goal": "g", "mode": "quantum"}]}})

    def test_invalid_output_schema_rejected_locatably(self) -> None:
        with pytest.raises(ValueError, match=r"steps\[0\]\.output_schema"):
            WorkflowChildTaskAdapter.plan(
                {"workflow": {"steps": [{"goal": "g", "output_schema": {"type": "not-a-type"}}]}}
            )

    def test_empty_artifact_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"steps\[0\]\.artifact"):
            WorkflowChildTaskAdapter.plan(
                {"workflow": {"steps": [{"goal": "g", "artifact": "   "}]}},
            )

    def test_reserved_step_fields_rejected(self):
        with pytest.raises(ValueError, match="reserved"):
            WorkflowChildTaskAdapter.plan({"workflow": {"steps": [{"goal": "g", "workflow_parent": {}}]}})


class TestArtifactValidation:
    """Validation gating via the dispatcher helper (pure function surface)."""

    def _dispatcher(self):
        from hecate.execution.task_dispatcher import PlatformTaskDispatcher as TaskDispatcher

        return TaskDispatcher.__new__(TaskDispatcher)  # only the pure helper is used

    def test_output_schema_violation_reported(self) -> None:
        dispatcher = self._dispatcher()
        error = dispatcher._validate_step_artifacts(
            None,
            None,
            {"output_schema": {"type": "object", "required": ["report"]}},
            {"content": "not json"},
        )
        assert error is not None and "not valid JSON" in error

    def test_output_schema_violation_on_wrong_shape(self) -> None:
        dispatcher = self._dispatcher()
        error = dispatcher._validate_step_artifacts(
            None,
            None,
            {"output_schema": {"type": "object", "required": ["report"]}},
            {"content": '{"wrong": 1}'},
        )
        assert error is not None and "violates the declared schema" in error

    def test_valid_output_passes(self) -> None:
        dispatcher = self._dispatcher()
        error = dispatcher._validate_step_artifacts(
            None,
            None,
            {"output_schema": {"type": "object", "required": ["report"]}},
            {"content": '{"report": "done"}'},
        )
        assert error is None

    def test_missing_artifact_reported(self) -> None:
        dispatcher = self._dispatcher()
        error = dispatcher._validate_step_artifacts(
            None, None, {"artifact": "report"}, {"content": "x", "artifacts": {"other": "data"}}
        )
        assert error is not None and "missing" in error

    def test_artifact_present_passes(self) -> None:
        dispatcher = self._dispatcher()
        error = dispatcher._validate_step_artifacts(
            None, None, {"artifact": "report"}, {"content": "x", "artifacts": {"report": "data"}}
        )
        assert error is None

    def test_undeclared_step_passes(self) -> None:
        dispatcher = self._dispatcher()
        assert dispatcher._validate_step_artifacts(None, None, {}, {"content": "anything"}) is None


class TestWorkflowRouting:
    async def test_validation_failure_routes_to_await(self):
        """The dispatcher's workflow branch routes a validation failure to the
        await path when on_failure=await — exercised through the full branch
        with a fake control service (no child is submitted for a failed
        step)."""
        from hecate.contracts.execution.references import BackendRef, RefKind
        from hecate.execution.task_dispatcher import PlatformTaskDispatcher as TaskDispatcher
        from hecate.execution.workflow_child import WorkflowChildTaskAdapter

        plan = WorkflowChildTaskAdapter.plan(
            {
                "workflow": {
                    "steps": [
                        {
                            "goal": "produce report",
                            "output_schema": {"type": "object", "required": ["report"]},
                        },
                        {"goal": "next step"},
                    ],
                    "on_failure": "await",
                }
            }
        )
        dispatcher = TaskDispatcher.__new__(TaskDispatcher)
        submitted: list = []

        class _Svc:
            async def submit(self, **kwargs):
                submitted.append(kwargs)

                class _R:
                    task_ref = BackendRef(RefKind.TASK, "iss", "child-1")

                return _R()

        dispatcher._task_control = lambda db: _Svc()

        class _Ctx:
            workflow_plan = plan
            provided = {
                "step_index": 0,
                "child_outcome": {
                    "child_state": "succeeded",
                    "content": "missing the report",
                    "artifacts": {},
                },
            }
            workspace_id = __import__("uuid").uuid4()
            agent_id = __import__("uuid").uuid4()

        outcome = await TaskDispatcher._execute_workflow(
            dispatcher, None, _Ctx(), task_ref=BackendRef(RefKind.TASK, "iss", "parent"), lease=None
        )
        assert outcome["status"] == "awaiting_reconciliation"
        assert "source=validation" in outcome["content"]
        assert submitted == []  # a failed step never submits the next one

    async def test_success_advances_to_next_step(self):
        from hecate.contracts.execution.references import BackendRef, RefKind
        from hecate.execution.task_dispatcher import PlatformTaskDispatcher as TaskDispatcher
        from hecate.execution.workflow_child import WorkflowChildTaskAdapter

        plan = WorkflowChildTaskAdapter.plan(
            {
                "workflow": {
                    "steps": [
                        {
                            "goal": "produce report",
                            "output_schema": {"type": "object", "required": ["report"]},
                            "artifact": "report",
                        },
                        {"goal": "next step"},
                    ],
                    "on_failure": "fail",
                }
            }
        )
        dispatcher = TaskDispatcher.__new__(TaskDispatcher)
        submitted: list = []

        class _Svc:
            async def submit(self, **kwargs):
                submitted.append(kwargs)

                class _R:
                    task_ref = BackendRef(RefKind.TASK, "iss", "child-2")

                return _R()

        dispatcher._task_control = lambda db: _Svc()

        class _Ctx:
            workflow_plan = plan
            provided = {
                "step_index": 0,
                "child_outcome": {
                    "child_state": "succeeded",
                    "content": '{"report": "done"}',
                    "artifacts": {"report": "data"},
                },
            }
            workspace_id = __import__("uuid").uuid4()
            agent_id = __import__("uuid").uuid4()

        import pytest

        from hecate.execution.task_dispatcher import TaskWaitingSignalError

        with pytest.raises(TaskWaitingSignalError):
            # The validated success parks the parent on the NEXT child.
            await TaskDispatcher._execute_workflow(
                dispatcher,
                None,
                _Ctx(),
                task_ref=BackendRef(RefKind.TASK, "iss", "parent"),
                lease=None,
            )
        assert len(submitted) == 1  # exactly the next step's child

    async def test_execution_failure_distinct_from_validation(self):
        from hecate.contracts.execution.references import BackendRef, RefKind
        from hecate.execution.task_dispatcher import PlatformTaskDispatcher as TaskDispatcher
        from hecate.execution.workflow_child import WorkflowChildTaskAdapter

        plan = WorkflowChildTaskAdapter.plan({"workflow": {"steps": [{"goal": "g"}], "on_failure": "await"}})
        dispatcher = TaskDispatcher.__new__(TaskDispatcher)
        dispatcher._task_control = lambda db: None

        class _Ctx:
            workflow_plan = plan
            provided = {
                "step_index": 0,
                "child_outcome": {"child_state": "failed", "content": ""},
            }
            workspace_id = __import__("uuid").uuid4()
            agent_id = __import__("uuid").uuid4()

        outcome = await TaskDispatcher._execute_workflow(
            dispatcher, None, _Ctx(), task_ref=BackendRef(RefKind.TASK, "iss", "p"), lease=None
        )
        assert outcome["status"] == "awaiting_reconciliation"
        assert "source=execution" in outcome["content"]


@pytest.mark.parametrize("policy, expected", [("fail", "failed"), ("await", "reconciliation_required")])
async def test_recorded_invalid_child_cannot_be_replaced_by_forged_summary(harness, monkeypatch, policy, expected):
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    import uuid

    from hecate.contracts.execution.durable import CommandState
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from tests.test_execution.conftest import HARNESS_WS
    from tests.test_execution.test_workflow_child_adapter import (
        _children_of,
        _FinalAnswerLLM,
        _service_of,
        _submit_workflow,
        _worker_of,
    )

    monkeypatch.setattr("hecate_llm.service.llm_service", _FinalAnswerLLM())
    parent = await _submit_workflow(
        harness,
        [
            {"goal": "report", "artifact": "report", "output_schema": {"type": "object", "required": ["report"]}},
            {"goal": "must never execute"},
        ],
        on_failure=policy,
    )
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    state = harness["store"].get_task_state(parent.task_ref)
    token = state.extra["wait"]["wait_token"]
    child = _children_of(harness, parent.task_ref)[0][0]

    async def defer_callback(self, db, context, outcome):
        pass

    with monkeypatch.context() as patches:
        patches.setattr(PlatformTaskDispatcher, "_issue_child_callback", defer_callback)
        assert await _worker_of(harness).dispatch_once(child)
    assert harness["store"].get_task_state(child).lifecycle_state.value == "succeeded"
    async with harness["session_factory"]() as db:
        svc = _service_of(harness, db)
        forged_index = await svc.submit_workflow_callback(
            workspace_id=HARNESS_WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="wrong-step",
            wait_token=token,
            child_task_id=uuid.UUID(child.id),
            declared_state="succeeded",
            payload={"step_index": 1},
        )
        assert forged_index.record.state is CommandState.REJECTED
        accepted = await svc.submit_workflow_callback(
            workspace_id=HARNESS_WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="honest-state-forged-content",
            wait_token=token,
            child_task_id=uuid.UUID(child.id),
            declared_state="succeeded",
            payload={"step_index": 0},
            result_summary={"content": '{"report":"forged"}', "artifacts": {"report": "forged"}},
        )
        assert accepted.record.state is CommandState.APPLIED
    persisted = harness["store"].get_task_input(parent.task_ref)["provided"]["child_outcome"]
    assert persisted["content"] == "child done" and persisted["artifacts"] == {}
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == expected
    assert state.extra["result"]["failure_source"] == "validation"
    assert len(_children_of(harness, parent.task_ref)) == 1


async def test_validated_multistep_restarts_and_callback_receipts(harness, monkeypatch):
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    import uuid

    from hecate.contracts.execution.durable import CommandState
    from tests.test_execution.conftest import HARNESS_WS
    from tests.test_execution.test_workflow_child_adapter import (
        _children_of,
        _FinalAnswerLLM,
        _service_of,
        _submit_workflow,
        _worker_of,
    )

    class StubJSONLLM(_FinalAnswerLLM):
        async def chat_stream(self, **kwargs):
            yield {"content": '{"report": "real-result"}', "tool_calls": None}

    monkeypatch.setattr("hecate_llm.service.llm_service", StubJSONLLM())
    step = {
        "goal": "report",
        "mode": "chat",
        "output_schema": {"type": "object", "required": ["report"]},
        "artifact": "report",
    }
    parent = await _submit_workflow(harness, [step, step])
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    child = _children_of(harness, parent.task_ref)[0][0]
    child_input = harness["store"].get_task_input(child)
    assert child_input["workflow_step"] == step
    state = harness["store"].get_task_state(parent.task_ref)
    token = state.extra["wait"]["wait_token"]
    assert await _worker_of(harness).dispatch_once(child)
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.extra["_workflow_callbacks"]["0"]["child_task_id"] == child.id
    assert state.extra["_workflow_callbacks"]["0"]["applied_fact_id"] == f"wf-callback-{child.id}-{token}"
    async with harness["session_factory"]() as db:
        duplicate = await _service_of(harness, db).submit_workflow_callback(
            workspace_id=HARNESS_WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="duplicate-before-advance",
            wait_token=token,
            child_task_id=uuid.UUID(child.id),
            declared_state="succeeded",
            payload={"step_index": 0},
        )
        assert duplicate.record.state is CommandState.REJECTED
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    revision = harness["store"].get_task_state(parent.task_ref).revision
    async with harness["session_factory"]() as db:
        late = await _service_of(harness, db).submit_workflow_callback(
            workspace_id=HARNESS_WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="late-old-child",
            wait_token=token,
            child_task_id=uuid.UUID(child.id),
            declared_state="succeeded",
            payload={"step_index": 0},
        )
        assert late.record.state is CommandState.REJECTED
    assert harness["store"].get_task_state(parent.task_ref).revision == revision
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 2
    assert await _worker_of(harness).dispatch_once(children[1][0])
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "succeeded"
    assert set(state.extra["_workflow_callbacks"]) == {"0", "1"}
    assert len(_children_of(harness, parent.task_ref)) == 2


async def test_fast_child_terminal_before_parent_wait_is_not_lost(harness, monkeypatch):
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    from hecate.execution.workflow_child import WorkflowChildTaskAdapter
    from tests.test_execution.test_workflow_child_adapter import (
        _children_of,
        _FinalAnswerLLM,
        _submit_workflow,
        _worker_of,
    )

    monkeypatch.setattr("hecate_llm.service.llm_service", _FinalAnswerLLM())
    submit = WorkflowChildTaskAdapter.submit_step

    async def finish_before_parent_wait(*args, **kwargs):
        child = await submit(*args, **kwargs)
        assert await _worker_of(harness).dispatch_once(child)
        return child

    monkeypatch.setattr(WorkflowChildTaskAdapter, "submit_step", finish_before_parent_wait)
    parent = await _submit_workflow(harness, [{"goal": "fast child"}])
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "queued"
    assert "0" in state.extra["_workflow_callbacks"]
    assert await _worker_of(harness).dispatch_once(parent.task_ref)
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"
    assert len(_children_of(harness, parent.task_ref)) == 1
