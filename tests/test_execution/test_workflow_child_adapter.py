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

import os
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
    if harness["store"].get_task_state(child_ref).lifecycle_state.value != "succeeded":
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


class _ToolRoundLLM:
    """Run two tool rounds or finish after denial, consuming real receipts."""

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[list[dict]] = []

    @staticmethod
    def _saw_tool(messages: list) -> bool:
        receipts = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        return len(receipts) >= 2 or any(m.get("is_error") for m in receipts)

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        self.calls += 1
        self.inputs.append([dict(m) for m in messages])
        if tools and not self._saw_tool(messages):
            return SimpleNamespace(
                content="",
                model=model or "stub-model",
                tool_calls=[
                    SimpleNamespace(
                        id=f"call_{1 + sum(m.get('role') == 'tool' for m in messages)}",
                        type="function",
                        function=SimpleNamespace(name="stub_calculator", arguments='{"a": 2, "b": 3}'),
                    )
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )
        return SimpleNamespace(
            content="child done with " + ", ".join(str(m["content"]) for m in messages if m.get("role") == "tool"),
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        result = await self.chat(messages=messages, model=model, tools=tools)
        if result.tool_calls is None:
            yield {"content": result.content, "tool_calls": None, "finish_reason": "stop"}
            return
        yield {"content": "", "tool_calls": result.tool_calls, "finish_reason": "tool_calls"}


WF_PG_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")


@pytest.mark.skipif(
    not WF_PG_URL,
    reason="HECATE_STEP6_POSTGRES_URL not configured; the inline-dispatch tool ledger needs "
    "PostgreSQL's multi-writer locking (SQLite's single writer deadlocks the two sync "
    "connections inside one dispatch); skip is not a pass",
)
@pytest.mark.parametrize("action", ["allow", "deny", "ask"])
async def test_child_agent_multi_round_tools_via_real_engine(harness, monkeypatch, action) -> None:
    """G3 workflow-entry evidence: a deployed-agent child runs a REAL
    engine tool loop through the task-control chain — the tool executes
    exactly once, its receipt lands as paired engine events, the model's
    final answer consumes the real tool result, and the parent converges
    over the verified callback."""
    import hecate_llm.service as llm_module

    from hecate.core.composition import entry_assembly
    from hecate.models.agent_version import AgentVersionModel
    from hecate.models.run import RunModel
    from hecate.models.tool_policy import ToolPolicyRuleModel
    from hecate.runtime.eventstore import EventType
    from hecate.studio.event_state.postgres_store import PostgresEventStore
    from hecate.studio.session_state.postgres_store import PostgresSessionStateStore
    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS
    from hecate.tools.tool.registry import ToolRegistry
    from tests.test_channel.test_chat_engine_g3_entry import StubToolExecutor

    executor = StubToolExecutor()

    monkeypatch.setitem(
        BUILTIN_TOOL_DEFINITIONS,
        "stub_calculator",
        {
            "risk_level": "LOW",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
            "returns": {"type": "string"},
        },
    )

    def _build(db, skill_ref_manifest=None, *, workspace_id=None):  # noqa: ANN001 — test double
        registry = ToolRegistry(db=db, builtin_executor=executor, workspace_id=workspace_id)
        registry._builtin_names = {"stub_calculator"}
        return registry

    monkeypatch.setattr(entry_assembly, "build_tool_registry", _build)

    provider = _ToolRoundLLM()
    monkeypatch.setattr(llm_module, "llm_service", provider)

    # Freeze the tool onto the agent version so the dispatcher resolves it.
    async with harness["session_factory"]() as db:
        if action != "allow":
            db.add(ToolPolicyRuleModel(workspace_id=WS, tool_pattern="stub_calculator", action=action))
        version = (
            await db.execute(
                AgentVersionModel.__table__.select().where(AgentVersionModel.agent_id == harness["agent_id"])
            )
        ).first()
        from sqlalchemy import update

        await db.execute(
            update(AgentVersionModel)
            .where(AgentVersionModel.id == version.id)
            .values(config_snapshot={**(version.config_snapshot or {}), "tools": ["stub_calculator"]})
        )
        await db.commit()

    store = PostgresEventStore(harness["session_factory"])
    monkeypatch.setattr(entry_assembly, "_shared_event_store", store)
    monkeypatch.setattr(
        entry_assembly, "_shared_session_state_store", PostgresSessionStateStore(harness["session_factory"])
    )

    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())
    parent = await _submit_workflow(harness, [{"goal": "step one"}])
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 1
    child_ref, _index = children[0]

    if harness["store"].get_task_state(child_ref).lifecycle_state.value != "succeeded":
        assert await worker.dispatch_once(child_ref) is True
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "succeeded"
    for _ in range(3):
        if harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded":
            break
        assert await worker.dispatch_once(parent.task_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"

    # The tool ran exactly once through the real engine chain and the
    # final answer consumed its real result.
    expected_calls = 2 if action == "allow" else 0
    assert executor.calls == [("stub_calculator", {"a": 2, "b": 3})] * expected_calls
    assert provider.calls == (3 if action == "allow" else 2)
    receipts = [m for m in provider.inputs[-1] if m.get("role") == "tool"]
    if action == "allow":
        assert [m["content"] for m in receipts] == ["5", "5"]
    else:
        assert receipts[-1].get("is_error")
        assert any(word in receipts[-1]["content"].lower() for word in ("denied", "rejected", "approval", "policy")), (
            receipts
        )

    # Engine parity: the child's session log carries the paired receipt.
    from sqlalchemy import select

    async with harness["session_factory"]() as db:
        run = (await db.execute(select(RunModel).where(RunModel.task_id == uuid.UUID(child_ref.id)))).scalar_one()
        assert run.deployment_id is not None
        assert run.projection["state"] == "succeeded"
    reader = PostgresEventStore(harness["session_factory"])
    new_events = await reader.get_events(uuid.UUID(run.backend_ref["id"]))
    kinds = [e.event_type for e in new_events]
    if action == "allow":
        calls = [e.payload["tool_call_id"] for e in new_events if e.event_type is EventType.TOOL_CALL]
        results = [e.payload["tool_call_id"] for e in new_events if e.event_type is EventType.TOOL_RESULT]
        assert calls == results == ["call_1", "call_2"]
    assert (EventType.APPROVAL_ASKED in kinds) == (action == "ask")
    assert (EventType.APPROVAL_DECIDED in kinds) == (action == "ask")
    if action != "allow":
        assert any(
            event.event_type is EventType.CHANNEL_WRITE
            and event.payload.get("channel") == "messages"
            and receipts[-1] in event.payload.get("value", [])
            for event in new_events
        ), "the child's denial receipt must survive a fresh PostgreSQL read"
    if action == "ask":
        decisions = [event.payload for event in new_events if event.event_type is EventType.APPROVAL_DECIDED]
        assert len(decisions) == 1 and decisions[0]["approved"] is False
    # No re-execution across the parent wake: still exactly one tool call.
    assert await worker.dispatch_once(parent.task_ref) is False
    assert len(executor.calls) == expected_calls
