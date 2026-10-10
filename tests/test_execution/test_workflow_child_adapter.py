"""Workflow child-task adapter acceptance (step6e).

Real chain, no ``_execute`` monkeypatching: a platform task whose input
declares ``workflow.steps`` is driven by the named adapter — every step
is submitted as a REAL child task through ``TaskControlService.submit``,
the parent parks on the child's ``await_task_ref``, and the child's own
terminal path issues the verified auto-callback (platform issuer) that
wakes the parent with the child's outcome. Scenarios: single step,
multi-step ordering with forged-callback rejection, failure convergence
per ``on_failure``, and both-side process restarts. Background spawns
are suppressed and a worker drives dispatches explicitly so each
transition is observed deterministically; the execution itself is the
real entry service (provider boundary stubbed only, the G3 precedent).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState
from hecate.execution.task_control import TaskControlService
from tests.test_execution.conftest import HARNESS_WS as WS


class _FinalAnswerLLM:
    """Provider-boundary double: one model turn, final answer, no tools."""

    def __init__(self, explode: bool = False) -> None:
        self._explode = explode
        self.calls = 0

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        self.calls += 1
        if self._explode:
            raise RuntimeError("model exploded")
        return SimpleNamespace(
            content="child done",
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        self.calls += 1
        if self._explode:
            raise RuntimeError("model exploded")
        yield {"content": "child done", "tool_calls": None}
        yield {"content": "", "finish_reason": "stop", "tool_calls": None}


@pytest.fixture
def llm_stub(monkeypatch: pytest.MonkeyPatch) -> _FinalAnswerLLM:
    stub = _FinalAnswerLLM()
    import hecate_llm.service as llm_module

    monkeypatch.setattr(llm_module, "llm_service", stub)
    return stub


def _dispatcher_of(harness):
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    return PlatformTaskDispatcher(harness["store"], harness["session_factory"])


def _worker_of(harness) -> Any:  # noqa: ANN401 — test helper
    from hecate_durable.worker import DurableWorker

    return DurableWorker(harness["store"], _dispatcher_of(harness), leases=harness["store"].leases)


def _service_of(harness, db):
    return TaskControlService(
        db,
        store=harness["store"],
        recorder=harness["store"],
        backend="postgres",
        ledger_source="core",
        session_factory=harness["session_factory"],
    )


async def _submit_workflow(harness, steps: list[dict], *, on_failure: str = "fail") -> Any:
    """Submit the parent workflow task; spawns suppressed by the harness."""

    import hecate.execution.task_control as task_control_module

    task_control_module._spawn_background = lambda coro: coro.close()  # noqa: SLF001 — harness suppresses spawns
    async with harness["session_factory"]() as db:
        result = await _service_of(harness, db).submit(
            workspace_id=WS,
            user_id=None,  # system-initiated
            goal="parent workflow",
            agent_id=harness["agent_id"],
            input={
                "messages": [{"role": "user", "content": "run parent workflow"}],
                "workflow": {"steps": steps, "on_failure": on_failure},
            },
        )
    return result


def _children_of(harness, parent_ref) -> list[Any]:

    children = []
    for record in harness["store"].list_tasks():
        ref = record.task_ref
        if ref.id == parent_ref.id:
            continue
        payload = harness["store"].get_task_input(ref) or {}
        stamp = payload.get("workflow_parent") or {}
        if (stamp.get("task_ref") or {}).get("id") == parent_ref.id:
            children.append((ref, int(stamp.get("step_index"))))
    return sorted(children, key=lambda pair: pair[1])


async def test_single_step_workflow_parks_child_and_parent_succeeds(harness, llm_stub, monkeypatch) -> None:
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    parent = await _submit_workflow(harness, [{"goal": "step one"}])
    worker = _worker_of(harness)

    # First dispatch: the adapter submits the REAL child and parks the
    # parent on its await_task_ref contract.
    assert await worker.dispatch_once(parent.task_ref) is True
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "waiting_input"
    wait = (state.extra or {}).get("wait") or {}
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 1
    child_ref, step_index = children[0]
    assert step_index == 0
    from hecate_durable.contracts.references import task_ref as mk_task_ref

    assert wait["contract_ref"]["await_task_ref"] == mk_task_ref("hecate", str(child_ref.id)).to_dict()
    assert wait.get("consumed") is False
    child_input = harness["store"].get_task_input(child_ref) or {}
    assert child_input["workflow_parent"]["task_ref"]["id"] == parent.task_ref.id
    assert child_input["workflow_parent"]["step_index"] == 0

    # The child executes for real and its terminal path auto-calls the
    # parent: requeued with the verified outcome, token consumed once.
    assert await worker.dispatch_once(child_ref) is True
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "succeeded"
    parent_state = harness["store"].get_task_state(parent.task_ref)
    assert parent_state.lifecycle_state.value == "queued"
    assert ((parent_state.extra or {}).get("wait") or {}).get("consumed") is True

    # The woken parent advances past the final step and succeeds.
    assert await worker.dispatch_once(parent.task_ref) is True
    final = harness["store"].get_task_state(parent.task_ref)
    assert final.lifecycle_state.value == "succeeded"
    final_input = harness["store"].get_task_input(parent.task_ref) or {}
    assert final_input["provided"]["child_outcome"]["child_state"] == "succeeded"
    assert final_input["provided"]["step_index"] == 0
    assert len(_children_of(harness, parent.task_ref)) == 1, "no step may be re-submitted"


async def test_multi_step_order_and_forged_callback_rejected(harness, llm_stub, monkeypatch) -> None:
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    parent = await _submit_workflow(harness, [{"goal": "step one"}, {"goal": "step two"}])
    worker = _worker_of(harness)

    assert await worker.dispatch_once(parent.task_ref) is True
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "waiting_input"
    wait_token = ((state.extra or {}).get("wait") or {})["wait_token"]
    parent_id = uuid.UUID(parent.task_ref.id)

    # A forged callback (child does not exist / not the contract child) is
    # rejected without consuming the token or moving the parent.
    async with harness["session_factory"]() as db:
        forged = await _service_of(harness, db).submit_workflow_callback(
            workspace_id=WS,
            task_id=parent_id,
            command_id="forged-unknown-child",
            wait_token=wait_token,
            child_task_id=uuid.uuid4(),
            declared_state="succeeded",
        )
    assert forged.record.state is CommandState.REJECTED
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "waiting_input"

    children = _children_of(harness, parent.task_ref)
    assert len(children) == 1 and children[0][1] == 0  # step two not submitted yet
    child_one = children[0][0]

    assert await worker.dispatch_once(child_one) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "queued"
    assert len(_children_of(harness, parent.task_ref)) == 1

    assert await worker.dispatch_once(parent.task_ref) is True
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "waiting_input"
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 2 and children[1][1] == 1  # strictly ordered
    child_two = children[1][0]

    assert await worker.dispatch_once(child_two) is True
    assert await worker.dispatch_once(parent.task_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"
    final_input = harness["store"].get_task_input(parent.task_ref) or {}
    assert final_input["provided"]["step_index"] == 1
    assert len(_children_of(harness, parent.task_ref)) == 2


async def test_failed_step_converges_per_on_failure(harness, monkeypatch) -> None:
    import hecate_llm.service as llm_module

    for on_failure, expected in (("fail", "failed"), ("await", "reconciliation_required")):
        monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
        stub = _FinalAnswerLLM(explode=True)
        monkeypatch.setattr(llm_module, "llm_service", stub)
        parent = await _submit_workflow(harness, [{"goal": "doomed"}, {"goal": "never"}], on_failure=on_failure)
        worker = _worker_of(harness)

        assert await worker.dispatch_once(parent.task_ref) is True
        children = _children_of(harness, parent.task_ref)
        assert len(children) == 1
        child_ref = children[0][0]

        # The child's execution failure converges deterministically to a
        # failed terminal, whose auto-callback the parent consumes.
        assert await worker.dispatch_once(child_ref) is True
        assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "failed"
        parent_state = harness["store"].get_task_state(parent.task_ref)
        assert parent_state.lifecycle_state.value == "queued", f"scenario {on_failure}"

        assert await worker.dispatch_once(parent.task_ref) is True
        final = harness["store"].get_task_state(parent.task_ref)
        assert final.lifecycle_state.value == expected, f"scenario {on_failure}"
        assert len(_children_of(harness, parent.task_ref)) == 1, "remaining steps must not be submitted"


async def test_both_side_restarts_keep_the_chain(harness, llm_stub, monkeypatch) -> None:
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    parent = await _submit_workflow(harness, [{"goal": "step one"}, {"goal": "step two"}])
    worker_a = _worker_of(harness)

    # Parent platform "process" A: parks on step one.
    assert await worker_a.dispatch_once(parent.task_ref) is True
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 1
    child_one = children[0][0]

    # Child platform "process" B (fresh dispatcher/worker): completes the
    # child and issues the cross-process auto-callback.
    worker_b = _worker_of(harness)
    assert await worker_b.dispatch_once(child_one) is True
    assert harness["store"].get_task_state(child_one).lifecycle_state.value == "succeeded"
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "queued"

    # Parent platform "process" C: advances to step two and parks again.
    worker_c = _worker_of(harness)
    assert await worker_c.dispatch_once(parent.task_ref) is True
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 2 and children[1][1] == 1
    child_two = children[1][0]

    # Child process D completes step two; parent process E finishes.
    worker_d = _worker_of(harness)
    assert await worker_d.dispatch_once(child_two) is True
    worker_e = _worker_of(harness)
    assert await worker_e.dispatch_once(parent.task_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"
    assert len(_children_of(harness, parent.task_ref)) == 2, "restarts must not re-submit steps"
