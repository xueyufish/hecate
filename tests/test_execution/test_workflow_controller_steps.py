"""Explicit ``mode: "controller"`` workflow steps (sequential adapter slice).

A controller step is a REAL platform child task running the durable
controller chain on the SAME agent: tool feedback drives further turns,
accepted decisions replay after a crash, and the parent advances only on
the child's verified callback plus the declared delivery contract. The
step's declared budget slice reuses the shared workspace quota and the
durable slice ledger (never a parallel one); the parent accumulates the
trusted spend. Provider responses and the web_search effect are doubles;
everything else is the real chain.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from hecate.execution.workflow_child import WorkflowChildTaskAdapter
from tests.test_execution.conftest import HARNESS_ORG as ORG
from tests.test_execution.conftest import HARNESS_OWNER as OWNER
from tests.test_execution.conftest import HARNESS_WS as WS
from tests.test_execution.test_workflow_child_adapter import _children_of, _worker_of

# --- provider / tool doubles -------------------------------------------------

EFFECTS: dict[str, str] = {}


class _ControllerStepLLM:
    """Controller-chain double: the child asks for its tool, then answers
    from the tool result. The workflow parent never reaches the model."""

    def __init__(self) -> None:
        self.child_asks = 0
        self.chat_asks = 0
        self.nonstream_calls = 0
        self.calls: list[list[str]] = []

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        self.calls.append(named)
        prompt = str([m for m in messages if isinstance(m, dict) and m.get("role") == "user"][:1])
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        # Only the controller step drives the tool round; the chat step's
        # single turn answers directly from its goal text.
        if "web_search" in named and not tool_messages and "research" in prompt:
            self.child_asks += 1
            yield {"content": "", "tool_calls": [_delta(0, "call-1", "web_search", {"query": "stock"})]}
            return
        self.chat_asks += 1
        yield {"content": json.dumps({"report": "child done"}), "tool_calls": None}

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        # Chat-mode steps keep their historical single-turn, non-streaming
        # execution.
        self.nonstream_calls += 1
        self.chat_asks += 1
        return SimpleNamespace(
            content="summary done",
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )


def _delta(index: int, call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


@pytest.fixture
def llm_stub(monkeypatch: pytest.MonkeyPatch) -> _ControllerStepLLM:
    import hecate_llm.service as llm_module

    stub = _ControllerStepLLM()
    monkeypatch.setattr(llm_module, "llm_service", stub)
    return stub


@pytest.fixture(autouse=True)
def _tools_and_effects(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> dict[str, Any]:
    """Faithful tool double: only agents declaring web_search see it, and the
    effect appends a durable line so tests can count real executions."""

    import hecate.core.composition.entry_assembly as entry_assembly
    from hecate.tools.tool.builtin import BuiltInToolExecutor

    async def _load_tools(db, tool_names, *, workspace_id=None):
        if "web_search" not in (tool_names or []):
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]

    monkeypatch.setattr(entry_assembly, "load_agent_tools", _load_tools)
    effects = tmp_path / "effects.jsonl"
    EFFECTS["path"] = str(effects)
    real_execute = BuiltInToolExecutor.execute

    async def effect(self, name, args, context=None):
        if name != "web_search":
            return await real_execute(self, name, args, context)
        with open(effects, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"query": args.get("query")}) + "\n")
        return {"results": ["found"]}

    monkeypatch.setattr(BuiltInToolExecutor, "execute", effect)
    return {"effects": str(effects)}


@pytest.fixture(autouse=True)
def _suppress_inline_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())


async def _agent_with_tools(harness, name: str) -> Any:
    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel

    async with harness["session_factory"]() as db:
        agent = AgentModel(workspace_id=WS, name=name)
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub", "tools": ["web_search"]},
            content_hash="c" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id, agent_id=agent.id, workspace_id=WS, organization_id=ORG, owner_user_id=OWNER
            )
        )
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=WS,
                backend_type=BackendType.BUILTIN,
                access_mode=AccessMode.IN_PROCESS,
                issuer_domain="hecate",
                capability_snapshot={},
                axes_harness="hecate",
                axes_environment="none",
                axes_tool_execution="hecate_gateway",
                is_default=True,
            )
        )
        await db.commit()
        return agent.id


async def _submit_workflow(harness, agent_id, steps: list[dict], *, user_id: Any = None, **workflow_extra: Any) -> Any:
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
        result = await service.submit(
            workspace_id=WS,
            user_id=user_id,
            goal="parent workflow",
            agent_id=agent_id,
            input={
                "messages": [{"role": "user", "content": "run parent workflow"}],
                "workflow": {"steps": steps, **workflow_extra},
            },
        )
    return result


def _controller_step(goal: str = "research the stock", **extra: Any) -> dict[str, Any]:
    return {"goal": goal, "mode": "controller", **extra}


async def _holds(harness, parent_ref) -> list[Any]:
    from sqlalchemy import select

    from hecate.models.quota import QuotaReservationModel

    async with harness["session_factory"]() as db:
        return list(
            (
                await db.scalars(
                    select(QuotaReservationModel).where(
                        QuotaReservationModel.invocation_key.like(f"workflow-step:{parent_ref.id}:%")
                    )
                )
            ).all()
        )


# --- declaration validation -----------------------------------------------------


@pytest.mark.parametrize(
    "workflow",
    [
        {"steps": [{"goal": "x", "mode": "controller", "agent_children": {"targets": []}}]},
        {"steps": [{"goal": "x", "mode": "controller", "controller": {"max_turns": 3}}]},
        {"steps": [{"goal": "x", "budget_units": 5}]},
        {"steps": [_controller_step(budget_units=0)]},
        {"steps": [_controller_step(budget_units=True)]},
        {"steps": [_controller_step(max_turns=0)]},
        {"steps": [_controller_step(max_turns="many")]},
        {"steps": [_controller_step(deadline="not-a-timestamp")]},
        {"steps": [_controller_step(budget_units=10)], "budget_units": 5},
        {"steps": [_controller_step(budget_units=10)], "budget_units": True},
    ],
)
def test_invalid_controller_declarations_are_rejected(workflow: dict) -> None:
    with pytest.raises(ValueError):
        WorkflowChildTaskAdapter.plan({"workflow": workflow})


def test_valid_controller_plan_parses_the_budget_and_deadline() -> None:
    plan = WorkflowChildTaskAdapter.plan(
        {
            "workflow": {
                "steps": [_controller_step(budget_units=400, deadline="2030-01-01T00:00:00+00:00", max_turns=3)],
                "budget_units": 400,
                "deadline": "2030-01-02T00:00:00+00:00",
            }
        }
    )
    assert plan is not None and plan.budget_units == 400 and plan.deadline.endswith("+00:00")
    assert plan.steps[0]["mode"] == "controller"


def test_workflow_total_requires_slices_on_every_controller_step() -> None:
    with pytest.raises(ValueError, match="every controller step"):
        WorkflowChildTaskAdapter.plan(
            {"workflow": {"steps": [_controller_step(budget_units=10), _controller_step()], "budget_units": 40}}
        )


async def test_client_cannot_mint_a_workflow_budget_slice(harness) -> None:
    """The public submission input cannot grant the host's budget override."""
    from hecate.execution.task_control import TaskControlService, TaskControlValidationError

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db, store=harness["store"], recorder=harness["store"], backend="postgres", ledger_source="core"
        )
        with pytest.raises(TaskControlValidationError, match="reserved for the host adapter"):
            await service.submit(
                workspace_id=WS,
                user_id=OWNER,
                agent_id=harness["agent_id"],
                goal="forged step",
                input={
                    "messages": [{"role": "user", "content": "forged step"}],
                    "controller_loop": True,
                    "workflow_step": _controller_step(budget_units=50000),
                },
            )
    assert not await asyncio.to_thread(harness["store"].list_tasks)


# --- the real chain --------------------------------------------------------------


async def test_controller_step_runs_the_real_chain_and_advances_the_parent(harness, llm_stub, monkeypatch) -> None:
    agent_id = await _agent_with_tools(harness, "workflow-controller-agent")
    human = uuid.uuid5(ORG, "human-initiator")
    parent = await _submit_workflow(
        harness,
        agent_id,
        [_controller_step("research the stock", artifact="report"), {"goal": "summarize the findings"}],
        user_id=human,
    )
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True

    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 1 and children[0][1] == 0
    child_ref = children[0][0]
    child_input = await asyncio.to_thread(harness["store"].get_task_input, child_ref)
    # The child is a REAL controller task on the same agent, carrying the
    # trusted parent stamp and the human initiator of the chain.
    assert child_input["controller_loop"] is True
    assert child_input["controller"]["max_turns"] == 8
    assert child_input["workflow_parent"]["task_ref"]["id"] == parent.task_ref.id
    assert child_input["workflow_parent"]["initiator_user_id"] == str(human)
    assert child_input["identity_chain"]["initiator"] == str(human)
    assert child_input["workflow_step"]["mode"] == "controller"

    # The controller continues on the REAL tool feedback: one tool round,
    # one final answer — the parent never reached the model.
    assert await worker.dispatch_once(child_ref) is True
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_state.lifecycle_state.value == "succeeded", child_state.extra
    assert child_state.extra["result"]["artifacts"] == {"report": "child done"}
    assert len(llm_stub.calls) == 2, "the controller step makes exactly the tool turn and the tool-result turn"
    with open(EFFECTS["path"], encoding="utf-8") as handle:
        effects = [json.loads(line) for line in handle if line.strip()]
    assert len(effects) == 1

    # The parent advanced only on the verified callback; the step's delivery
    # fact and the applied callback are durable on the parent.
    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state.value == "queued"
    callbacks = (state.extra or {}).get("_workflow_callbacks") or {}
    assert "0" in callbacks
    assert await worker.dispatch_once(parent.task_ref) is True
    # Step two is an ordinary chat step: submitted and parked as before.
    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state.value == "waiting_input"
    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 2 and children[1][1] == 1
    step_two = children[1][0]
    step_two_input = await asyncio.to_thread(harness["store"].get_task_input, step_two)
    assert step_two_input["controller_loop"] is False, "chat steps keep their historical execution"
    assert await worker.dispatch_once(step_two) is True
    assert await worker.dispatch_once(parent.task_ref) is True
    final = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    assert llm_stub.chat_asks == 2, "the controller tool-result turn and the chat step each answer directly"
    assert len(await asyncio.to_thread(_children_of, harness, parent.task_ref)) == 2


# --- budget -----------------------------------------------------------------------


async def test_declared_slices_persist_and_accumulate_on_the_parent(harness, llm_stub) -> None:
    agent_id = await _agent_with_tools(harness, "workflow-budget-agent")
    parent = await _submit_workflow(
        harness,
        agent_id,
        [_controller_step("research the stock", budget_units=50000)],
        budget_units=50000,
    )
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    assert await worker.dispatch_once(child_ref) is True
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_state.lifecycle_state.value == "succeeded", child_state.extra
    # The durable slice block lives on the child row and the trusted figure
    # rides the verified callback.
    assert (child_state.extra or {}).get("budget_slice", {}).get("allocated") == 50000
    assert 0 < (child_state.extra or {}).get("result", {}).get("budget", {}).get("used", 0) < 50000

    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state.value == "queued"
    used = (state.extra or {}).get("workflow_budget_used")
    assert isinstance(used, int) and 0 < used < 50000, state.extra
    holds = await _holds(harness, parent.task_ref)
    assert len(holds) == 1 and holds[0].settled and not holds[0].released, holds

    assert await worker.dispatch_once(parent.task_ref) is True
    final = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra


async def test_insufficient_workspace_budget_refuses_before_the_child_exists(harness) -> None:
    from hecate.models.quota import QuotaModel
    from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE

    agent_id = await _agent_with_tools(harness, "workflow-poor-agent")
    per_call = 10000
    async with harness["session_factory"]() as db:
        db.add(
            QuotaModel(
                name="cost",
                resource_type="cost",
                scope="workspace",
                scope_id=WS,
                limit_value=per_call * MODEL_TOKEN_UNIT_SCALE * 0.5,
                window_type="monthly",
                workspace_id=WS,
            )
        )
        await db.commit()
    parent = await _submit_workflow(harness, agent_id, [_controller_step("x", budget_units=per_call)])
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state.value == "failed", state.extra
    assert (state.extra or {}).get("result", {}).get("failure_source") == "budget", state.extra
    assert len(await asyncio.to_thread(_children_of, harness, parent.task_ref)) == 0
    assert await _holds(harness, parent.task_ref) == [], "a refused step leaves no orphan hold"


async def test_a_failed_submit_releases_the_step_hold(harness, monkeypatch) -> None:
    from hecate.execution.task_control import TaskControlService

    agent_id = await _agent_with_tools(harness, "workflow-release-agent")
    parent = await _submit_workflow(harness, agent_id, [_controller_step("x", budget_units=20000)])
    real_submit = TaskControlService.submit

    async def exploding_submit(self, *args, **kwargs):
        if kwargs.get("idempotency_key", "").startswith("workflow:"):
            raise OSError("injected submission storage failure")
        return await real_submit(self, *args, **kwargs)

    monkeypatch.setattr(TaskControlService, "submit", exploding_submit)
    worker = _worker_of(harness)
    # The dispatch failure is a retryable worker outcome; the durable fact
    # under test is the hold released by the adapter's submit-failure path.
    assert await worker.dispatch_once(parent.task_ref) is False
    holds = await _holds(harness, parent.task_ref)
    assert holds and all(row.released for row in holds), holds


async def test_lost_submission_confirmation_keeps_the_existing_hold(harness, llm_stub, monkeypatch) -> None:
    """An exception after child commit must retain the live child's hold."""
    from hecate.execution.task_control import TaskControlService

    agent_id = await _agent_with_tools(harness, "workflow-committed-step")
    parent = await _submit_workflow(harness, agent_id, [_controller_step(budget_units=50000)])
    submit = TaskControlService.submit

    async def submit_then_lose_response(self, **kwargs):
        await submit(self, **kwargs)
        raise OSError("lost confirmation after durable submit")

    with monkeypatch.context() as patch:
        patch.setattr(TaskControlService, "submit", submit_then_lose_response)
        assert await _worker_of(harness).dispatch_once(parent.task_ref) is False

    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 1
    holds = await _holds(harness, parent.task_ref)
    assert len(holds) == 1 and not holds[0].released
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(
        harness["store"],
        PlatformTaskDispatcher(harness["store"], harness["session_factory"]),
        leases=harness["store"].leases,
        clock=lambda: datetime.now(UTC) + timedelta(minutes=1),
    )
    assert await worker.dispatch_once(parent.task_ref) is True
    assert await worker.dispatch_once(children[0][0]) is True
    assert await worker.dispatch_once(parent.task_ref) is True
    assert (await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)).lifecycle_state.value == (
        "succeeded"
    )
    assert await asyncio.to_thread(_children_of, harness, parent.task_ref) == children


# --- cancel and deadline -----------------------------------------------------------


async def test_parent_cancel_records_a_requested_cancel_on_the_running_step(harness, llm_stub, monkeypatch) -> None:
    from hecate.tools.tool.builtin import BuiltInToolExecutor

    release = asyncio.Event()
    entered = asyncio.Event()
    real_execute = BuiltInToolExecutor.execute

    async def gated_execute(self, name, args, context=None):
        if name != "web_search":
            return await real_execute(self, name, args, context)
        entered.set()
        await release.wait()
        return {"results": ["found"]}

    monkeypatch.setattr(BuiltInToolExecutor, "execute", gated_execute)

    agent_id = await _agent_with_tools(harness, "workflow-cancel-agent")
    parent = await _submit_workflow(harness, agent_id, [_controller_step("research the stock", budget_units=50000)])
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    driver = asyncio.create_task(worker.dispatch_once(child_ref))
    await asyncio.wait_for(entered.wait(), timeout=30)

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
        issued = await service.issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
        )
    assert issued.record.state is not CommandState.REJECTED
    # The running step holds a REAL cancel request — requested, not stopped.
    receipt = await service.get_command(
        workspace_id=WS, command_id=f"workflow-cancel:{parent.task_ref.id}:{child_ref.id}"
    )
    assert receipt.state is CommandState.REQUESTED
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_state.lifecycle_state.value == "running", child_state.extra

    release.set()
    assert await driver is True
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_state.lifecycle_state.value == "cancelled", child_state.extra
    assert llm_stub.child_asks == 1, "the cancelled step never reaches a second model turn"
    holds = await _holds(harness, parent.task_ref)
    assert len(holds) == 1 and holds[0].settled and holds[0].settled_units > 0
    assert (await service.get_command(workspace_id=WS, command_id=receipt.command_id)).state is CommandState.APPLIED

    parent_state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert parent_state.lifecycle_state.value == "cancelled"

    # A late verified callback cannot re-activate the terminal parent.
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )
        late = await service.submit_workflow_callback(
            workspace_id=WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="late-wf-callback",
            wait_token=str((((parent_state.extra or {}).get("wait") or {}).get("wait_token")) or ""),
            child_task_id=uuid.UUID(child_ref.id),
            declared_state="cancelled",
        )
    assert late.record.state is CommandState.REJECTED
    assert (await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)).lifecycle_state.value == (
        "cancelled"
    )


async def test_workflow_deadline_gates_the_advance_without_a_child(harness) -> None:
    agent_id = await _agent_with_tools(harness, "workflow-deadline-agent")
    past = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    parent = await _submit_workflow(harness, agent_id, [_controller_step("x")], deadline=past, on_failure="await")
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state.value == "reconciliation_required", state.extra
    assert len(await asyncio.to_thread(_children_of, harness, parent.task_ref)) == 0


async def test_step_deadline_stops_the_controller_without_model_work(harness, llm_stub) -> None:
    agent_id = await _agent_with_tools(harness, "workflow-step-deadline-agent")
    past = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    parent = await _submit_workflow(
        harness, agent_id, [_controller_step("research the stock", deadline=past, max_turns=3)]
    )
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    assert await worker.dispatch_once(child_ref) is True
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert llm_stub.child_asks == 0, "an expired step deadline never reaches the model"
    assert child_state.lifecycle_state.value == "failed"
    assert child_state.extra["result"]["error_category"] == "controller_deadline_exceeded"
    # The parent still converges on the child's terminal fact.
    assert (await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)).lifecycle_state.value == (
        "queued"
    )


async def test_parent_deadline_bounds_an_already_submitted_step(harness, llm_stub, monkeypatch) -> None:
    """A queued child cannot start new model work after the parent's deadline."""
    import hecate_runtime.agent_controller as controller_module

    agent_id = await _agent_with_tools(harness, "workflow-inherited-deadline")
    deadline = datetime.now(UTC) + timedelta(hours=1)
    parent = await _submit_workflow(
        harness,
        agent_id,
        [_controller_step(deadline=(deadline + timedelta(hours=1)).isoformat())],
        deadline=deadline.isoformat(),
    )
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    payload = await asyncio.to_thread(harness["store"].get_task_input, child_ref)
    assert payload["controller"]["deadline"] == deadline.isoformat()

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return deadline + timedelta(seconds=1)

    monkeypatch.setattr(controller_module, "datetime", ExpiredClock)
    assert await worker.dispatch_once(child_ref) is True
    assert not llm_stub.calls
    child = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child.lifecycle_state.value == "failed"


async def test_controller_step_parks_for_approval_under_the_workflow_deadline(harness, llm_stub) -> None:
    """A protected action parks within the deadline and resumes on the same run."""
    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from hecate.execution.task_control import TaskControlService
    from hecate.models.tool_policy import ToolPolicyRuleModel

    agent_id = await _agent_with_tools(harness, "workflow-approval-controller")
    async with harness["session_factory"]() as db:
        db.add(ToolPolicyRuleModel(workspace_id=WS, tool_pattern="web_search", action="ask", priority=100))
        await db.commit()
    deadline = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    parent = await _submit_workflow(
        harness, agent_id, [_controller_step(approval_wait=True)], user_id=OWNER, deadline=deadline
    )
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    assert await worker.dispatch_once(child_ref) is True
    child = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL, child.extra
    assert child.extra["wait"]["wait_expires_at"] == deadline
    assert child.extra["wait"]["contract_ref"]["approval_ref"]
    assert llm_stub.child_asks == 1
    assert not Path(EFFECTS["path"]).exists()

    child_run = await asyncio.to_thread(harness["store"].run_for_task, child_ref)
    wait = child.extra["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        await SqlApprovalLedger(db).decide(approval_ref, approved=True, reason="reviewed", decided_by="reviewer")
        await db.commit()
        service = TaskControlService(
            db, store=harness["store"], recorder=harness["store"], backend="postgres", ledger_source="core"
        )
        resume = await service.issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(child_ref.id),
            kind=ControlCommandKind.RESUME,
            issuer="reviewer",
            detail_ns={"wait_token": wait["wait_token"]},
        )
    assert resume.record.state is CommandState.APPLIED, resume.detail
    assert await worker.dispatch_once(child_ref) is True
    child = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child.lifecycle_state is TaskLifecycleState.SUCCEEDED, child.extra
    assert await asyncio.to_thread(harness["store"].run_for_task, child_ref) == child_run
    assert llm_stub.child_asks == 1
    assert len(Path(EFFECTS["path"]).read_text(encoding="utf-8").splitlines()) == 1
    assert await worker.dispatch_once(parent.task_ref) is True
    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert await asyncio.to_thread(harness["store"].run_for_task, parent.task_ref) == parent.run_ref


async def test_outbox_retries_cancel_and_settles_an_unstarted_step(harness, monkeypatch) -> None:
    """A lost cancel notification cannot strand the queued step's quota hold."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_control import TaskControlService

    agent_id = await _agent_with_tools(harness, "workflow-cancel-recovery")
    parent = await _submit_workflow(harness, agent_id, [_controller_step(budget_units=50000)])
    assert await _worker_of(harness).dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    async def lost_cancel(*_args, **_kwargs):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(TaskControlService, "_cancel_pending_workflow_child", lost_cancel)
        async with harness["session_factory"]() as db:
            service = TaskControlService(
                db,
                store=harness["store"],
                recorder=harness["store"],
                backend="postgres",
                ledger_source="core",
                session_factory=harness["session_factory"],
            )
            await service.issue_command(
                workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
            )

    child = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child.lifecycle_state is TaskLifecycleState.QUEUED
    relay = OutboxRelay(
        harness["store"].session_factory,
        PlatformOutboxProjector(harness["store"], harness["session_factory"]),
        relay_key="workflow-cancel-recovery",
    )
    await relay.pump_once()
    child = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child.lifecycle_state is TaskLifecycleState.CANCELLED
    holds = await _holds(harness, parent.task_ref)
    assert len(holds) == 1 and holds[0].settled and holds[0].settled_units == 0


async def test_outbox_converges_a_child_with_an_unknown_outcome(harness) -> None:
    """A step that needs reconciliation cannot leave its parent waiting forever."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector

    agent_id = await _agent_with_tools(harness, "workflow-unknown-recovery")
    parent = await _submit_workflow(harness, agent_id, [_controller_step(budget_units=50000), _controller_step()])
    store = harness["store"]
    assert await _worker_of(harness).dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    await asyncio.to_thread(store.apply_task_state, child_ref, TaskLifecycleState.RUNNING)
    await asyncio.to_thread(store.apply_task_state, child_ref, TaskLifecycleState.RECONCILIATION_REQUIRED)

    relay = OutboxRelay(
        store.session_factory,
        PlatformOutboxProjector(store, harness["session_factory"]),
        relay_key="workflow-unknown-recovery",
    )
    await relay.pump_once()
    state = await asyncio.to_thread(store.get_task_state, parent.task_ref)
    assert state.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert state.extra["workflow_reconciliation_child"] == child_ref.to_dict()
    assert len(await asyncio.to_thread(_children_of, harness, parent.task_ref)) == 1
    holds = await _holds(harness, parent.task_ref)
    assert len(holds) == 1 and holds[0].unknown_hold and not holds[0].released


# --- recovery -----------------------------------------------------------------------


async def test_a_lost_step_callback_converges_exactly_once(harness, llm_stub, monkeypatch) -> None:
    parent_agent = await _agent_with_tools(harness, "workflow-recovery-agent")
    parent = await _submit_workflow(harness, parent_agent, [_controller_step("research the stock", budget_units=50000)])
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _index = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    async def silent_callback(self, db, context, outcome, *, retry_errors=False):
        return None

    real_callback = PlatformTaskDispatcher._issue_child_callback
    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_child_callback", silent_callback
    )
    assert await worker.dispatch_once(child_ref) is True
    child_state = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    assert child_state.lifecycle_state.value == "succeeded"

    # Recovery rides the SAME outbox path production uses (event_relay's
    # run_terminal handler): a fresh dispatcher re-issues the verified
    # callback from the child's committed terminal payload, retry_errors on.
    # A repeated redelivery is absorbed by the single-use wait token.
    monkeypatch.setattr("hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_child_callback", real_callback)
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector

    for relay_key in ("workflow-callback", "workflow-callback-redelivery"):
        relay = OutboxRelay(
            harness["store"].session_factory,
            PlatformOutboxProjector(harness["store"], harness["session_factory"]),
            relay_key=relay_key,
        )
        await relay.pump_once()
    state = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert state.lifecycle_state.value == "queued", state.extra
    callbacks = (state.extra or {}).get("_workflow_callbacks") or {}
    assert sorted(callbacks) == ["0"], callbacks

    assert await worker.dispatch_once(parent.task_ref) is True
    final = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    used = (final.extra or {}).get("workflow_budget_used")
    assert isinstance(used, int) and used > 0, final.extra
