"""Persistent platform Agent child calls (parent delegation, verified wake).

Real chain, provider boundary stubbed only: a controller task whose input
declares ``controller.agent_children`` delegates through the host's
durable ``delegate_agent`` tool — a REAL child task is submitted under the
call's idempotency key, the parent parks on the ``agent_child`` wait, the
child executes with the parent's REAL initiator and the declared tool
narrowing, and its terminal path issues the verified callback that wakes
the parent with the child's committed result as a durable
``agent_child_result`` revision. Scenarios: the happy delegation, crash
windows around the acceptance and the callback, repeated/concurrent and
forged callbacks, refusal paths without side effects, cancel propagation,
the parent deadline convergence, and the read-only query surface.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.execution.agent_child import DELEGATE_AGENT_TOOL_NAME as DELEGATE_MARKER
from hecate.execution.task_control import TaskControlService, task_ref_of
from tests.test_execution.conftest import HARNESS_ORG as ORG
from tests.test_execution.conftest import HARNESS_OWNER as OWNER
from tests.test_execution.conftest import HARNESS_WS as WS


async def _budget_child(harness, *, units=10000):
    target_id = await _add_agent(harness, "budget-worker")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = units * 2
    declaration["targets"][0]["budget_units"] = units
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    return parent, child_ref, worker


async def _child_holds(harness, parent):
    from sqlalchemy import select

    from hecate.models.quota import QuotaReservationModel

    async with harness["session_factory"]() as db:
        return list(
            (
                await db.scalars(
                    select(QuotaReservationModel).where(
                        QuotaReservationModel.invocation_key.like(f"agent-child:{parent.task_ref.id}:%")
                    )
                )
            ).all()
        )


async def test_slice_concurrent_reservations_do_not_overwrite_each_other(harness, llm_stub, monkeypatch):
    from hecate_governance.usage import ReservationStatus

    from hecate.execution.agent_child import DurableSliceLedger

    _, child, _ = await _budget_child(harness)
    harness["store"].apply_task_state(child, TaskLifecycleState.RUNNING)
    ledgers = [DurableSliceLedger(store=harness["store"], task_ref=child, allocation=10) for _ in range(2)]
    gate = asyncio.Event()
    loaded = 0

    for ledger in ledgers:
        original = ledger._load

        async def simultaneous_load(original=original):
            nonlocal loaded
            state = await original()
            loaded += 1
            if loaded == 2:
                gate.set()
            await gate.wait()
            return state

        monkeypatch.setattr(ledger, "_load", simultaneous_load)
    results = await asyncio.gather(*(ledger.reserve(f"attempt-{i}", 6) for i, ledger in enumerate(ledgers)))
    assert [r.status for r in results].count(ReservationStatus.RESERVED) == 1
    assert [r.status for r in results].count(ReservationStatus.INSUFFICIENT) == 1
    assert await ledgers[0].remaining() == 4


async def test_slice_allocation_mismatch_is_not_a_fresh_budget(harness, llm_stub):
    from hecate.execution.agent_child import DurableSliceLedger

    _, child, _ = await _budget_child(harness)
    harness["store"].apply_task_state(
        child,
        TaskLifecycleState.RUNNING,
        extra_update={
            "budget_slice": {"allocated": 12, "entries": {"attempt": {"reserved": 8, "settled": 8, "status": "actual"}}}
        },
    )
    ledger = DurableSliceLedger(store=harness["store"], task_ref=child, allocation=10)
    with pytest.raises(ValueError, match="allocation"):
        await ledger.remaining()


def test_budget_total_cannot_leave_an_unmetered_target():
    from hecate.execution.agent_child import agent_child_policy

    declaration = json.loads(json.dumps(DECLARATION))
    declaration["targets"][0]["agent_id"] = str(uuid.uuid4())
    declaration["budget_units"] = 100
    with pytest.raises(ValueError, match="every target"):
        agent_child_policy(declaration)


async def test_policy_refusal_does_not_leave_an_open_quota_hold(harness, monkeypatch):
    from hecate.execution.agent_child import AgentChildDelegateTool, agent_child_policy

    async def deny(*args, **kwargs):
        return "tool denied by the parent"

    monkeypatch.setattr("hecate.execution.agent_child.verify_delegable_tools", deny)
    target_id = await _add_agent(harness, "budget-worker")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 100
    declaration["targets"][0]["budget_units"] = 50
    parent = await _submit_parent(harness, declaration)
    tool = AgentChildDelegateTool(
        policy=agent_child_policy(declaration),
        parent_ref=parent.task_ref,
        parent_goal="x",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    result = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "x"}}, {"invocation_id": "deny"}
    )
    assert result["status"] == "agent_child_refused"
    assert not [r for r in await _child_holds(harness, parent) if not r.released and not r.settled]


async def test_failed_quota_settlement_does_not_consume_the_wait(harness, llm_stub, monkeypatch):
    from hecate_governance.usage import SettleOutcome, SettleStatus

    async def conflict(*args, **kwargs):
        return SettleOutcome(SettleStatus.CONFLICT, None)

    parent, child, worker = await _budget_child(harness)
    monkeypatch.setattr("hecate.ops.budget_reservations.QuotaBudgetLedger.settle", conflict)
    await worker.dispatch_once(child)
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert harness["store"].list_input_revisions(parent.task_ref) == []


async def test_corrupt_slice_converges_to_reconciliation_without_refunding(harness, llm_stub, monkeypatch):
    from hecate_durable.storage.models import TaskStateRow

    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    async def no_callback(*args, **kwargs):
        return None

    parent, child, worker = await _budget_child(harness)
    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", no_callback
    )
    await worker.dispatch_once(child)
    with harness["store"].session_factory() as db:
        row = db.get(TaskStateRow, (child.issuer_domain, child.id))
        row.extra = {**row.extra, "budget_slice": {"allocated": 0, "entries": {}}}
        db.commit()
    await AgentChildRunReconciler(harness["store"], harness["session_factory"]).converge_once()
    assert (
        harness["store"].get_task_state(parent.task_ref).lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    )
    assert all(not row.released and not row.settled for row in await _child_holds(harness, parent))


async def test_parent_cancel_still_closes_the_unexecuted_child_hold(harness, llm_stub):
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    parent, child, _ = await _budget_child(harness)
    async with harness["session_factory"]() as db:
        await _service_of(harness, db).issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
        )
    await AgentChildRunReconciler(harness["store"], harness["session_factory"]).converge_once()
    assert harness["store"].get_task_state(child).lifecycle_state is TaskLifecycleState.CANCELLED
    assert not [r for r in await _child_holds(harness, parent) if not r.released and not r.settled]


async def test_unknown_slice_attempt_cannot_be_released(harness, llm_stub):
    from hecate_governance.usage import ReleaseStatus, UsageRecord, UsageStatus

    from hecate.execution.agent_child import DurableSliceLedger

    _, child, _ = await _budget_child(harness)
    harness["store"].apply_task_state(child, TaskLifecycleState.RUNNING)
    ledger = DurableSliceLedger(store=harness["store"], task_ref=child, allocation=10)
    await ledger.reserve("unknown", 6)
    await ledger.settle(UsageRecord("unknown", 0, UsageStatus.UNKNOWN))
    assert await ledger.release("unknown") is ReleaseStatus.HELD_FOR_RECONCILIATION
    assert await ledger.remaining() == 4


@pytest.mark.parametrize(
    "block",
    [
        {"allocated": 10, "entries": []},
        {"allocated": 10, "entries": {"bad": {"reserved": -2, "settled": None}}},
        {"allocated": 10, "entries": {"bad": {"reserved": 2, "settled": True}}},
    ],
)
def test_malformed_budget_facts_cannot_become_zero_usage(block):
    from hecate.execution.agent_child import derive_slice_usage

    with pytest.raises(ValueError):
        derive_slice_usage(block, allocated=10)
    assert derive_slice_usage(None, allocated=10)["status"] == "unknown"
    assert derive_slice_usage(None, allocated=10)["used"] == 10


async def test_failed_release_keeps_the_delegation_retryable(harness, monkeypatch):
    from hecate.execution.agent_child import AgentChildDelegateTool, agent_child_policy
    from hecate.execution.task_control import TaskControlValidationError
    from hecate.ops.budget_reservations import QuotaBudgetLedger

    async def refuse(*args, **kwargs):
        raise TaskControlValidationError("target refused")

    target_id = await _add_agent(harness, "budget-worker")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 100
    declaration["targets"][0]["budget_units"] = 50
    parent = await _submit_parent(harness, declaration)
    tool = AgentChildDelegateTool(
        policy=agent_child_policy(declaration),
        parent_ref=parent.task_ref,
        parent_goal="x",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    real_release = QuotaBudgetLedger.release
    failures = 0

    async def flaky_release(self, key):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("release storage unavailable")
        return await real_release(self, key)

    monkeypatch.setattr(TaskControlService, "submit", refuse)
    monkeypatch.setattr(QuotaBudgetLedger, "release", flaky_release)
    args = {"alias": "worker", "input": {"task": "x"}}
    with pytest.raises(OSError, match="storage unavailable"):
        await tool.execute("delegate_agent", args, {"invocation_id": "release-retry"})
    result = await tool.execute("delegate_agent", args, {"invocation_id": "release-retry"})
    assert result["status"] == "agent_child_refused"
    assert all(row.released for row in await _child_holds(harness, parent))


async def test_created_child_prevents_refunding_a_submission_error(harness, llm_stub):
    from hecate.execution.agent_child import AgentChildDelegateTool, agent_child_policy

    parent, child, _ = await _budget_child(harness)
    payload = harness["store"].get_task_input(child)
    declaration = harness["store"].get_task_input(parent.task_ref)["controller"]["agent_children"]
    tool = AgentChildDelegateTool(
        policy=agent_child_policy(declaration),
        parent_ref=parent.task_ref,
        parent_goal="x",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    with pytest.raises(ValueError, match="already exists"):
        await tool._release_call_budget(call_id=payload["agent_child_parent"]["call_id"])
    assert all(not row.released and not row.settled for row in await _child_holds(harness, parent))


async def _run_child_process(harness, module: str, *args: str, timeout: float = 60) -> Any:
    """Run a ``python -m`` helper against the harness database in a fresh interpreter.

    The database binds through the child environment (``_agent_child_db``):
    ``str(engine.url)`` masks the password, so argv could never carry a
    PostgreSQL DSN, and argv is visible host-wide anyway.
    """

    import subprocess
    import sys
    from pathlib import Path

    from tests.test_execution._agent_child_db import child_environment

    return await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", module, *args],
        cwd=Path(__file__).resolve().parents[2],
        env=child_environment(harness),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


async def test_budget_reservations_survive_an_abrupt_process_exit(harness, llm_stub):
    from hecate_governance.usage import ReservationStatus

    from hecate.execution.agent_child import DurableSliceLedger

    _, child, _ = await _budget_child(harness)
    harness["store"].apply_task_state(child, TaskLifecycleState.RUNNING)
    completed = await _run_child_process(harness, "tests.test_execution._agent_child_budget_process", child.id, "die")
    assert completed.returncode == 73, completed.stderr
    ledger = DurableSliceLedger(store=harness["store"], task_ref=child, allocation=10)
    summary = await ledger.snapshot()
    assert summary["used"] == 8 and summary["unknown_count"] == 1
    assert (await ledger.reserve("retry", 3)).status is ReservationStatus.INSUFFICIENT

    # The refusal holds from a THIRD interpreter too: the surviving facts,
    # not this process's memory, deny the retry before any model request.
    probe = await _run_child_process(harness, "tests.test_execution._agent_child_budget_process", child.id, "probe")
    assert probe.returncode == 0, probe.stderr
    report = json.loads(probe.stdout)
    assert report["backend"] == ("postgresql" if harness["pg"] else "sqlite")
    assert report["used"] == 8 and report["unknown_count"] == 1
    assert report["retry_status"] == "insufficient"


async def test_parent_deadline_still_closes_the_cancelled_child_hold(harness, llm_stub):
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    # Use the immutable deadline binding, not a rewritten wait contract.
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    target = await _add_agent(harness, "deadline-budget-worker")
    declaration = _declaration_of(harness, target)
    declaration["budget_units"] = 20000
    declaration["targets"][0]["budget_units"] = 10000
    parent = await _submit_parent(harness, declaration, controller_extra={"deadline": deadline.isoformat()})
    await _worker_of(harness).dispatch_once(parent.task_ref)
    child, _ = _children_of(harness, parent.task_ref)[0]
    reconciler = AgentChildRunReconciler(
        harness["store"], harness["session_factory"], clock=lambda: deadline + timedelta(seconds=1)
    )
    await reconciler.converge_once()
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state is TaskLifecycleState.FAILED
    assert harness["store"].get_task_state(child).lifecycle_state is TaskLifecycleState.CANCELLED
    assert all(row.settled or row.released for row in await _child_holds(harness, parent))


@pytest.mark.parametrize("status", ["estimated", "unverified"])
def test_budget_summary_preserves_uncertain_usage_quality(status):
    from hecate.execution.agent_child import derive_slice_usage

    block = {
        "allocated": 10,
        "entries": {
            "known": {"reserved": 4, "settled": 2, "status": "actual"},
            "uncertain": {"reserved": 4, "settled": 3, "status": status},
        },
    }
    assert derive_slice_usage(block, allocated=10)["status"] == status


DECLARATION = {
    "targets": [
        {
            "alias": "worker",
            "agent_id": "resolved-at-test",
            "allowed_tools": ["query_inventory"],
            "input_schema": {"type": "object", "required": ["task"], "properties": {"task": {"type": "string"}}},
            "output_schema": {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}},
        }
    ],
    "max_calls": 2,
}


async def _add_agent(harness, name: str, *, tools: list[str] | None = None) -> Any:
    """A second real agent (version + principal + default deployment)."""

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
            config_snapshot={"model": "stub", **({"tools": tools} if tools else {})},
            content_hash="b" * 64,
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


class _Delta:
    """One streamed tool-call delta (LiteLLM shape the adapter accumulates)."""

    def __init__(self, index: int, call_id: str, name: str, arguments: str) -> None:
        self.index = index
        self.id = call_id
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _AgentChildLLM:
    """Provider-boundary double: the parent delegates, the child answers.

    ``chat`` serves the child's chat-mode execution; ``chat_stream`` serves
    the parent's controller turns. The parent's final answer quotes the
    child's committed result — the delegation changed its next action.
    """

    def __init__(self, *, child_text: str = "child done", delegate_input: dict | None = None) -> None:
        self.child_text = child_text
        self.delegate_input = delegate_input or {"task": "summarize stock"}
        self.parent_delegate_asks = 0
        self.parent_finals = 0
        self.child_asks = 0
        self.child_saw_tools: list[list[str]] = []
        self.parent_saw_tools: list[list[str]] = []
        self.final_text: str | None = None

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        # The delegation tool's presence marks the PARENT's controller
        # turns; every other caller (the chat-mode child, with or without
        # its own narrowed tools) streams a plain child answer.
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        if DELEGATE_MARKER not in named:
            if named:
                self.child_saw_tools.append(named)
            self.child_asks += 1
            yield {"content": json.dumps({"answer": self.child_text}), "tool_calls": None}
            return
        self.parent_saw_tools.append(named)
        child_results = [m for m in messages if m.get("role") == "user" and "child_task_id" in str(m.get("content"))]
        if child_results:
            self.parent_finals += 1
            result = json.loads(child_results[-1]["content"])["provided_input"]
            self.final_text = json.dumps({"answer": f"child said {result['content']}"})
            yield {"content": self.final_text, "tool_calls": None}
            return
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if not tool_messages:
            self.parent_delegate_asks += 1
            yield {
                "content": "",
                "tool_calls": [
                    _Delta(0, "call-1", "delegate_agent", json.dumps({"alias": "worker", "input": self.delegate_input}))
                ],
            }
            return
        if any("agent_child_refused" in str(m.get("content")) for m in tool_messages):
            # A refusal is model-visible data: finish without the delegation.
            self.parent_finals += 1
            self.final_text = json.dumps({"answer": "delegation refused; finishing without it"})
            yield {"content": self.final_text, "tool_calls": None}
            return
        # The acceptance envelope is history but no verified result yet:
        # never answer past an accepted call.
        yield {"content": "", "tool_calls": None}


@pytest.fixture
def llm_stub(monkeypatch: pytest.MonkeyPatch):
    import hecate_llm.service as llm_module

    stub = _AgentChildLLM()
    monkeypatch.setattr(llm_module, "llm_service", stub)
    return stub


@pytest.fixture(autouse=True)
def _suppress_inline_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive this module's tasks explicitly without leaking the patch into other suites."""
    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())


def _worker_of(harness) -> Any:
    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    return DurableWorker(
        harness["store"],
        PlatformTaskDispatcher(harness["store"], harness["session_factory"]),
        leases=harness["store"].leases,
    )


def _service_of(harness, db) -> TaskControlService:
    return TaskControlService(
        db,
        store=harness["store"],
        recorder=harness["store"],
        backend="postgres",
        ledger_source="core",
        session_factory=harness["session_factory"],
    )


def _declaration_of(harness, target_id: str, **overrides: Any) -> dict:
    declaration = json.loads(json.dumps(DECLARATION))
    declaration["targets"][0]["agent_id"] = str(target_id)
    declaration["targets"][0].update(overrides)
    return declaration


async def _submit_parent(harness, declaration: dict, **input_overrides: Any) -> Any:
    async with harness["session_factory"]() as db:
        result = await _service_of(harness, db).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="delegate to a sibling",
            agent_id=harness["agent_id"],
            input={
                "messages": [{"role": "user", "content": "delegate a task"}],
                "controller_loop": True,
                "controller": {
                    "max_turns": 8,
                    "agent_children": declaration,
                    **input_overrides.pop("controller_extra", {}),
                },
                **input_overrides,
            },
        )
    return result


async def test_child_output_schema_gates_its_success(harness, llm_stub) -> None:
    """A declared child standard must be evaluated before publishing successful delivery."""
    target_id = await _add_agent(harness, "worker-agent")
    llm_stub.child_text = 42
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    await worker.dispatch_once(child_ref)
    child = harness["store"].get_task_state(child_ref)
    assert child.lifecycle_state is TaskLifecycleState.FAILED
    assert child.extra["result"]["acceptance"]["status"] == "acceptance_failed"


async def test_callback_rejects_tampered_child_delivery(harness, llm_stub, monkeypatch) -> None:
    from hecate_durable.storage.models import TaskStateRow

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]

    async def no_callback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", no_callback
    )
    await worker.dispatch_once(child_ref)
    with harness["store"].session_factory() as db:
        row = db.get(TaskStateRow, (child_ref.issuer_domain, child_ref.id))
        row.extra = {**row.extra, "result": {**row.extra["result"], "content": '{"answer":"rewritten"}'}}
        db.commit()
    wait = harness["store"].get_task_state(parent.task_ref).extra["wait"]
    async with harness["session_factory"]() as db:
        receipt = await _service_of(harness, db).submit_agent_child_callback(
            workspace_id=WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="tampered-child",
            wait_token=wait["wait_token"],
            child_task_id=uuid.UUID(child_ref.id),
            declared_state="succeeded",
        )
    assert receipt.record.state is CommandState.REJECTED
    assert harness["store"].list_input_revisions(parent.task_ref) == []


async def test_indeterminate_child_converges_the_waiting_parent(harness, llm_stub) -> None:
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    await _worker_of(harness).dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    harness["store"].apply_task_state(child_ref, TaskLifecycleState.RECONCILIATION_REQUIRED)
    assert await AgentChildRunReconciler(harness["store"], harness["session_factory"]).converge_once() == 1
    assert (
        harness["store"].get_task_state(parent.task_ref).lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    )


async def test_failed_child_without_delivery_wakes_with_only_failure_facts(harness, llm_stub) -> None:
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    await _worker_of(harness).dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    harness["store"].apply_task_state(
        child_ref,
        TaskLifecycleState.FAILED,
        extra_update={"result": {"status": "failed", "content": "unverified", "error_category": "model_error"}},
    )
    assert await AgentChildRunReconciler(harness["store"], harness["session_factory"]).converge_once() == 1
    revision = harness["store"].list_input_revisions(parent.task_ref)[0]
    assert revision["content"]["content"] is None
    assert revision["content"]["child_state"] == "failed"


@pytest.mark.parametrize("field,value", [("wait_expires_at", "invalid"), ("output_schema", {"type": "number"})])
async def test_corrupt_wait_does_not_rewrite_the_accepted_call(harness, llm_stub, field, value) -> None:
    from hecate_durable.storage.models import TaskStateRow

    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    await _worker_of(harness).dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    with harness["store"].session_factory() as db:
        row = db.get(TaskStateRow, (parent.task_ref.issuer_domain, parent.task_ref.id))
        wait = dict(row.extra["wait"])
        if field == "output_schema":
            wait["contract_ref"] = {**wait["contract_ref"], field: value}
        else:
            wait[field] = value
        row.extra = {**row.extra, "wait": wait}
        db.commit()
    assert await AgentChildRunReconciler(harness["store"], harness["session_factory"]).converge_once() == 1
    assert (
        harness["store"].get_task_state(parent.task_ref).lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    )
    assert harness["store"].get_task_state(child_ref).lifecycle_state is TaskLifecycleState.QUEUED
    assert harness["store"].list_input_revisions(parent.task_ref) == []


@pytest.mark.parametrize("parent_state", [TaskLifecycleState.FAILED, TaskLifecycleState.RECONCILIATION_REQUIRED])
async def test_inactive_parent_prevents_queued_child_model_usage(harness, llm_stub, parent_state) -> None:
    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    harness["store"].apply_task_state(parent.task_ref, parent_state)
    await worker.dispatch_once(child_ref)
    assert llm_stub.child_asks == 0
    child = harness["store"].get_task_state(child_ref)
    assert child.lifecycle_state is TaskLifecycleState.FAILED
    assert child.extra["result"]["error_category"] == "parent_inactive"


async def test_expired_parent_does_not_consume_a_late_terminal_callback(harness, llm_stub, monkeypatch) -> None:
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    parent = await _submit_parent(
        harness, _declaration_of(harness, target_id), controller_extra={"deadline": deadline.isoformat()}
    )
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]

    async def no_callback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", no_callback
    )
    await worker.dispatch_once(child_ref)
    reconciler = AgentChildRunReconciler(
        harness["store"], harness["session_factory"], clock=lambda: deadline + timedelta(seconds=1)
    )
    assert await reconciler.converge_once() == 1
    record = harness["store"].get_task_state(parent.task_ref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["result"]["error_category"] == "agent_child_deadline"
    assert harness["store"].list_input_revisions(parent.task_ref) == []


def test_budget_declaration_binds_per_call_to_the_total() -> None:
    from hecate.execution.agent_child import agent_child_policy

    ok = agent_child_policy(
        {
            "targets": [{"alias": "w", "agent_id": str(uuid.uuid4()), "budget_units": 100}],
            "budget_units": 1000,
        }
    )
    assert ok["budget_units"] == 1000 and ok["targets"]["w"]["budget_units"] == 100
    with pytest.raises(ValueError, match="exceeds the task's total budget"):
        agent_child_policy(
            {
                "targets": [{"alias": "w", "agent_id": str(uuid.uuid4()), "budget_units": 5000}],
                "budget_units": 1000,
            }
        )


async def test_untrusted_parent_stamp_is_rejected_at_submission(harness) -> None:
    from hecate.execution.task_control import TaskControlValidationError

    async with harness["session_factory"]() as db:
        with pytest.raises(TaskControlValidationError, match="reserved"):
            await _service_of(harness, db).submit(
                workspace_id=WS,
                user_id=OWNER,
                agent_id=harness["agent_id"],
                goal="forged child",
                input={
                    "messages": [{"role": "user", "content": "work"}],
                    "agent_child_parent": {
                        "task_ref": {"kind": "task", "issuer_domain": "hecate", "id": str(uuid.uuid4())},
                        "call_id": "forged",
                        "alias": "worker",
                        "agent_id": str(harness["agent_id"]),
                        "input_digest": "d" * 64,
                    },
                },
            )
    assert harness["store"].list_tasks() == []


def _children_of(harness, parent_ref) -> list[Any]:
    from hecate.contracts.execution.references import BackendRef, RefKind

    children = []
    for record in harness["store"].list_tasks():
        ref = record.task_ref
        if ref.id == parent_ref.id:
            continue
        payload = harness["store"].get_task_input(ref) or {}
        stamp = payload.get("agent_child_parent") or {}
        if (stamp.get("task_ref") or {}).get("id") == parent_ref.id:
            children.append((BackendRef(RefKind.TASK, ref.issuer_domain, ref.id), stamp))
    return children


async def test_parent_delegates_and_consumes_the_verified_child_result(harness, llm_stub) -> None:
    """The declared target executes for real; the parent reasons over its result."""

    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id)
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)

    # First dispatch: the tool submits the REAL child and parks the parent.
    assert await worker.dispatch_once(parent.task_ref) is True
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "waiting_input"
    wait = (state.extra or {}).get("wait") or {}
    assert wait["wake_kind"] == "agent_child" and wait["consumed"] is False
    contract = wait["contract_ref"]
    assert contract["alias"] == "worker" and contract["call_id"]
    assert contract["output_schema"] == DECLARATION["targets"][0]["output_schema"]
    assert contract["target"]["agent_id"] == str(target_id)
    assert state.extra["agent_child_calls"] == 1
    children = _children_of(harness, parent.task_ref)
    assert len(children) == 1
    child_ref, stamp = children[0]
    assert contract["child_task_ref"]["id"] == child_ref.id
    assert stamp["input_digest"] == contract["input_digest"]

    # The child runs with the parent's REAL initiator and the target's own
    # principal — never the workflow child's system identity.
    child_payload = harness["store"].get_task_input(child_ref) or {}
    assert child_payload["user_id"] == str(OWNER)
    assert child_payload["identity_chain"]["initiator"] == str(OWNER)
    assert child_payload["agent_child_parent"]["call_id"] == contract["call_id"]
    assert child_payload["identity_chain"]["principal_id"] != "platform-system"

    # The child executes and its terminal path wakes the parent once.
    if harness["store"].get_task_state(child_ref).lifecycle_state.value != "succeeded":
        assert await worker.dispatch_once(child_ref) is True
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "succeeded"
    parent_state = harness["store"].get_task_state(parent.task_ref)
    assert parent_state.lifecycle_state.value == "queued"
    assert ((parent_state.extra or {}).get("wait") or {}).get("consumed") is True
    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref)
    assert [row["source"] for row in revisions] == ["agent_child_result"]
    assert revisions[0]["content"]["content"] == json.dumps({"answer": "child done"})
    assert revisions[0]["content"]["child_state"] == "succeeded"
    assert revisions[0]["content"]["usage"] is None  # unreported usage stays unknown

    # The parent's controller consumed the verified result and changed its
    # next action accordingly: exactly one more model ask, one final.
    assert await worker.dispatch_once(parent.task_ref) is True
    final = harness["store"].get_task_state(parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    assert llm_stub.final_text == json.dumps({"answer": "child said " + json.dumps({"answer": "child done"})})
    assert len(_children_of(harness, parent.task_ref)) == 1, "no duplicate child"


async def test_park_commit_failure_rediscovers_the_same_child(harness, llm_stub, monkeypatch) -> None:
    """A crash between the child creation and the wait commit re-binds, never duplicates."""

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)

    real_apply = harness["store"].apply_task_state
    parks: list[str] = []

    def flaky_apply(task_ref, target_state, **kwargs):
        if (
            target_state is TaskLifecycleState.WAITING_INPUT
            and (kwargs.get("extra_update") or {}).get("wait", {}).get("wake_kind") == "agent_child"
        ):
            parks.append(kwargs["extra_update"]["wait"]["contract_ref"]["child_task_ref"]["id"])
            if len(parks) == 1:
                raise OSError("injected park-commit crash")
        return real_apply(task_ref, target_state, **kwargs)

    monkeypatch.setattr(harness["store"], "apply_task_state", flaky_apply)
    await worker.dispatch_once(parent.task_ref)  # the injected crash fails the attempt
    record = harness["store"].get_task_state(parent.task_ref)
    assert record.lifecycle_state.value == "queued"

    # The settled acceptance action replays: no second child, same binding.
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        parent.task_ref,
        TaskLifecycleState.QUEUED,
        extra_update={"next_dispatch_at": None},
    )
    assert await worker.dispatch_once(parent.task_ref) is True
    assert len(parks) == 2 and parks[0] == parks[1]
    assert len(_children_of(harness, parent.task_ref)) == 1
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "waiting_input"


async def test_lost_callback_is_recovered_by_the_reconciler(harness, llm_stub, monkeypatch) -> None:
    """A child terminal whose callback never landed is consumed from durable facts."""

    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    async def silent_callback(self, db, context, outcome, *, retry_errors=False):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", silent_callback
    )
    assert await worker.dispatch_once(child_ref) is True
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "succeeded"
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "waiting_input"

    reconciler = AgentChildRunReconciler(harness["store"], harness["session_factory"])
    assert await reconciler.converge_once() == 1
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "queued"
    assert await reconciler.converge_once() == 0  # the QUEUED parent is out of scope

    assert await worker.dispatch_once(parent.task_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"


async def test_duplicate_and_forged_callbacks_consume_exactly_once(harness, llm_stub) -> None:
    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]
    token = ((harness["store"].get_task_state(parent.task_ref).extra or {}).get("wait") or {})["wait_token"]
    parent_id = uuid.UUID(parent.task_ref.id)

    # A forged callback (unknown child) is rejected without side effects.
    async with harness["session_factory"]() as db:
        forged = await _service_of(harness, db).submit_agent_child_callback(
            workspace_id=WS,
            task_id=parent_id,
            command_id="forged-agent-child",
            wait_token=token,
            child_task_id=uuid.uuid4(),
            declared_state="succeeded",
        )
    assert forged.record.state is CommandState.REJECTED
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "waiting_input"

    assert await worker.dispatch_once(child_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "queued"

    call_id = ((harness["store"].get_task_state(parent.task_ref).extra or {}).get("wait") or {})["contract_ref"][
        "call_id"
    ]
    async with harness["session_factory"]() as db:
        replay = await _service_of(harness, db).submit_agent_child_callback(
            workspace_id=WS,
            task_id=parent_id,
            command_id=f"agent-child-callback-{call_id}-{token}",
            wait_token=token,
            child_task_id=uuid.UUID(child_ref.id),
            declared_state="succeeded",
        )
    assert "idempotent replay" in (replay.detail or "")
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "queued"

    assert await worker.dispatch_once(parent.task_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"


async def test_concurrent_callback_recording_does_not_park_a_healthy_parent(harness, llm_stub, monkeypatch) -> None:
    """Two reconcilers reissue the same deterministic callback at once.

    The recorder keeps one content per command id and the two submissions
    differ only in ``issued_at``. The second must see an in-flight command
    — never a "different content" conflict that parks a parent whose child
    succeeded cleanly. The next pass applies the recorded command once.
    """

    from dataclasses import replace

    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    async def silent_callback(self, db, context, outcome, *, retry_errors=False):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", silent_callback
    )
    assert await worker.dispatch_once(child_ref) is True

    store = harness["store"]
    real_record = store.record
    raced = 0

    def record_after_a_concurrent_submitter(command):
        nonlocal raced
        if raced == 0 and command.command_id.startswith("agent-child-callback-"):
            raced += 1
            # The other reconciler's submission of the SAME command, stamped
            # a moment earlier, lands first.
            real_record(replace(command, issued_at="2026-01-01T00:00:00+00:00"))
        return real_record(command)

    monkeypatch.setattr(store, "record", record_after_a_concurrent_submitter)
    await AgentChildRunReconciler(store, harness["session_factory"]).converge_once()
    assert raced == 1
    record = await asyncio.to_thread(store.get_task_state, parent.task_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT, record.extra
    assert await asyncio.to_thread(store.list_input_revisions, parent.task_ref) == []

    # The next pass retries from the recorded command and applies it once.
    monkeypatch.setattr(store, "record", real_record)
    assert await AgentChildRunReconciler(store, harness["session_factory"]).converge_once() == 1
    record = await asyncio.to_thread(store.get_task_state, parent.task_ref)
    assert record.lifecycle_state is TaskLifecycleState.QUEUED, record.extra
    revisions = await asyncio.to_thread(store.list_input_revisions, parent.task_ref)
    assert [row["source"] for row in revisions] == ["agent_child_result"]
    assert await worker.dispatch_once(parent.task_ref) is True
    assert (await asyncio.to_thread(store.get_task_state, parent.task_ref)).lifecycle_state.value == "succeeded"


@pytest.mark.parametrize("already_recorded", [False, True])
async def test_concurrent_command_already_applied_replays_its_receipt(harness, monkeypatch, already_recorded):
    """A winner can commit the effect before the loser's record/get boundary."""
    from dataclasses import replace

    from hecate.contracts.execution.durable import ControlCommandRecord

    target = await _add_agent(harness, "race-worker")
    parent = await _submit_parent(harness, _declaration_of(harness, target))
    store = harness["store"]
    command_id = "completed-concurrent-cancel"
    run = await asyncio.to_thread(store.run_for_task, parent.task_ref)
    initial = ControlCommandRecord(
        command_id=command_id,
        kind=ControlCommandKind.CANCEL,
        issuer="race",
        task_ref=parent.task_ref,
        run_ref=run,
        issued_at="2026-01-01T00:00:00+00:00",
        state=CommandState.REQUESTED,
        extra={"workspace_id": str(WS)},
    )
    real_record = store.record
    if already_recorded:
        await asyncio.to_thread(real_record, initial)
    raced = False

    def apply_winner_before_record(command):
        nonlocal raced
        if command.command_id == command_id and not raced:
            raced = True
            if not already_recorded:
                real_record(replace(command, issued_at=initial.issued_at))
            current = store.get_task_state(parent.task_ref)
            store.apply_task_state(
                parent.task_ref,
                TaskLifecycleState.CANCELLED,
                expected_revision=current.revision,
                applied_command_id=command_id,
                event_run_ref=run,
            )
        return real_record(command)

    monkeypatch.setattr(store, "record", apply_winner_before_record)
    async with harness["session_factory"]() as db:
        result = await _service_of(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(parent.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer="race",
            command_id=command_id,
        )
    assert result.record.state is CommandState.APPLIED
    assert "effect_run_ref" in result.record.extra
    assert (await asyncio.to_thread(store.get_task_state, parent.task_ref)).revision == 1


@pytest.mark.parametrize("changed", ["issuer", "workspace", "revision", "payload"])
async def test_concurrent_command_with_different_intent_is_not_replayed(harness, monkeypatch, changed):
    """A competing insertion cannot use receipt replay to replace caller intent."""
    from dataclasses import replace

    target = await _add_agent(harness, "race-worker")
    parent = await _submit_parent(harness, _declaration_of(harness, target))
    store = harness["store"]
    real_record = store.record

    def conflicting_insert(command):
        changes = {
            "issuer": {"issuer": "another-issuer"},
            "workspace": {"extra": {"workspace_id": str(uuid.uuid4())}},
            "revision": {"expected_revision": 999},
            "payload": {"payload": {"different": True}, "payload_schema_ref": "urn:test:other"},
        }
        real_record(replace(command, **changes[changed]))
        return real_record(command)

    monkeypatch.setattr(store, "record", conflicting_insert)
    async with harness["session_factory"]() as db:
        with pytest.raises(ValueError, match="different content"):
            await _service_of(harness, db).issue_command(
                workspace_id=WS,
                task_id=uuid.UUID(parent.task_ref.id),
                kind=ControlCommandKind.CANCEL,
                issuer="race",
                command_id="conflicting-insertion",
            )
    assert (await asyncio.to_thread(store.get_task_state, parent.task_ref)).lifecycle_state is TaskLifecycleState.QUEUED


@pytest.mark.parametrize("authority", ["foreign-task", "stale", "existing-unknown", "non-idempotent"])
async def test_host_confirmation_rejects_invalid_authority_or_unknown_outcome(harness, authority):
    """Child-submission confirmation cannot bypass the existing ledger fences."""
    from hecate_durable.contracts.durable import ActionIntent, ActionOutcome, ActionOutcomeRecord, ToolSideEffectClass
    from hecate_durable.storage.lease import StaleFenceError, task_dispatch_lease_key

    target = await _add_agent(harness, "confirmation-worker")
    parent = await _submit_parent(harness, _declaration_of(harness, target))
    store = harness["store"]
    run = await asyncio.to_thread(store.run_for_task, parent.task_ref)
    await asyncio.to_thread(store.apply_task_state, parent.task_ref, TaskLifecycleState.RUNNING)
    key = f"confirmation-{uuid.uuid4()}"
    await asyncio.to_thread(
        store.record_intent_ex,
        ActionIntent(
            action_key=key,
            action_name="delegate_agent",
            arguments_digest="a" * 64,
            side_effect_class=(
                ToolSideEffectClass.NON_IDEMPOTENT_WRITE
                if authority == "non-idempotent"
                else ToolSideEffectClass.IDEMPOTENT_WRITE
            ),
        ),
        task_ref=parent.task_ref,
        run_ref=run,
    )
    lease_key = task_dispatch_lease_key(
        parent.task_ref.issuer_domain, str(uuid.uuid4()) if authority == "foreign-task" else parent.task_ref.id
    )
    lease = await asyncio.to_thread(store.leases.acquire, lease_key, "confirmation-test", 60)
    if authority == "stale":
        await asyncio.to_thread(store.leases.release, lease_key, lease.holder, fencing_token=lease.fencing_token)
    elif authority == "existing-unknown":
        await asyncio.to_thread(
            store.record_outcome_ex, ActionOutcomeRecord(action_key=key, outcome=ActionOutcome.UNKNOWN)
        )
    with pytest.raises((ValueError, StaleFenceError)):
        await asyncio.to_thread(
            store.record_outcome_ex,
            ActionOutcomeRecord(action_key=key, outcome=ActionOutcome.SUCCEEDED, result_digest="b" * 64),
            result_payload={"status": "agent_child_accepted"},
            lease=lease,
        )
    final = await asyncio.to_thread(store.get_run_action, run, key)
    if authority == "existing-unknown":
        assert final["last_outcome"]["outcome"] == "unknown"
    else:
        assert final.get("last_outcome") is None


async def test_refusals_carry_no_side_effects(harness, llm_stub) -> None:
    """Unknown aliases, schema violations and exhausted bounds refuse as data."""

    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id)
    declaration["targets"][0]["max_calls"] = 1
    parent = await _submit_parent(harness, declaration)

    from hecate.execution.agent_child import AgentChildDelegateTool, agent_child_policy

    tool = AgentChildDelegateTool(
        policy=agent_child_policy(json.loads(json.dumps(declaration))),
        parent_ref=parent.task_ref,
        parent_goal="delegate to a sibling",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    before = len(harness["store"].list_tasks())

    unknown = await tool.execute("delegate_agent", {"alias": "nope", "input": {"task": "x"}}, {"invocation_id": "c1"})
    assert unknown["status"] == "agent_child_refused"
    schema_bad = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"wrong": "shape"}}, {"invocation_id": "c2"}
    )
    assert schema_bad["status"] == "agent_child_refused" and "schema" in schema_bad["reason"]
    extra_bad = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "x"}, "workspace_id": str(WS)}, {"invocation_id": "c2b"}
    )
    assert extra_bad["status"] == "agent_child_refused" and "only alias and input" in extra_bad["reason"]
    assert len(harness["store"].list_tasks()) == before, "refusals create nothing"

    accepted = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "summarize stock"}}, {"invocation_id": "c3"}
    )
    assert accepted["status"] == "agent_child_accepted"
    assert accepted["call"]["target"]["agent_id"] == str(target_id)

    second = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "again"}}, {"invocation_id": "c4"}
    )
    assert second["status"] == "agent_child_refused" and "already accepted" in second["reason"]
    assert len(harness["store"].list_tasks()) == before + 1

    # A fresh dispatch (the wake's replay view) reads the durable count:
    # with the target's declared bound reached, further calls refuse.
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        parent.task_ref,
        TaskLifecycleState.QUEUED,
        extra_update={"agent_child_calls": 1},
    )
    fresh = AgentChildDelegateTool(
        policy=tool._policy,
        parent_ref=parent.task_ref,
        parent_goal="delegate to a sibling",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    exhausted = await fresh.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "again"}}, {"invocation_id": "c9"}
    )
    assert exhausted["status"] == "agent_child_refused" and "bound" in exhausted["reason"]
    assert len(harness["store"].list_tasks()) == before + 1


async def test_target_bounds_are_independent_across_aliases(harness, llm_stub) -> None:
    from hecate.execution.agent_child import AgentChildDelegateTool, agent_child_policy

    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id, max_calls=1)
    declaration["targets"].append({**declaration["targets"][0], "alias": "reviewer"})
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    child_ref, _ = _children_of(harness, parent.task_ref)[0]
    await worker.dispatch_once(child_ref)
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state is TaskLifecycleState.QUEUED

    tool = AgentChildDelegateTool(
        policy=agent_child_policy(declaration),
        parent_ref=parent.task_ref,
        parent_goal="delegate to a sibling",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    exhausted = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "again"}}, {"invocation_id": "bounded-call"}
    )
    assert exhausted["status"] == "agent_child_refused" and "bound" in exhausted["reason"]
    accepted = await tool.execute(
        "delegate_agent", {"alias": "reviewer", "input": {"task": "check"}}, {"invocation_id": "review-call"}
    )
    assert accepted["status"] == "agent_child_accepted"
    assert accepted["call"]["alias"] == "reviewer"
    assert len(_children_of(harness, parent.task_ref)) == 2


async def test_cross_workspace_target_is_refused_without_side_effects(harness, llm_stub) -> None:
    """A declaration naming an agent from another workspace cannot submit."""

    from hecate.models.organization import OrganizationModel
    from hecate.models.workspace import WorkspaceModel

    other_ws = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
    other_org = uuid.UUID("00000000-0000-0000-0000-0000000000f2")
    async with harness["session_factory"]() as db:
        db.add(OrganizationModel(id=other_org, name="other", slug=f"org-other-{other_org.hex[:8]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=other_ws, org_id=other_org, name="other-ws", slug=f"ws-{other_ws.hex}"))
        await db.commit()
    foreign_agent = await _add_agent(harness, "foreign-agent")
    async with harness["session_factory"]() as db:
        from hecate.models.agent import AgentModel

        agent = await db.get(AgentModel, foreign_agent)
        agent.workspace_id = other_ws
        await db.commit()

    declaration = _declaration_of(harness, foreign_agent)
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)  # the refusal fails the ask; retry converges
    state = harness["store"].get_task_state(parent.task_ref)
    if state.lifecycle_state.value == "queued":
        await asyncio.to_thread(
            harness["store"].apply_task_state,
            parent.task_ref,
            TaskLifecycleState.QUEUED,
            extra_update={"next_dispatch_at": None},
        )
        await worker.dispatch_once(parent.task_ref)
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state.value == "succeeded", state.extra
    assert (state.extra or {}).get("wait") is None
    assert len(_children_of(harness, parent.task_ref)) == 0


async def test_parent_cancel_propagates_and_the_child_never_runs(harness, llm_stub) -> None:
    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    async with harness["session_factory"]() as db:
        await _service_of(harness, db).issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
        )
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "cancelled"

    # The not-yet-dispatched child is cancelled outright; no model usage.
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "cancelled"
    assert llm_stub.child_asks == 0


async def test_cancelled_parent_blocks_child_at_dispatch_entry(harness, llm_stub) -> None:
    """A previously claimed child checks its parent again on dispatch entry."""

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]
    # This covers a persisted RUNNING claim, not a cancellation during a live call.
    await asyncio.to_thread(harness["store"].apply_task_state, child_ref, TaskLifecycleState.RUNNING)
    async with harness["session_factory"]() as db:
        await _service_of(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(parent.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer="tester",
        )
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "cancelled"
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    record = await asyncio.to_thread(harness["store"].get_task_state, child_ref)
    await PlatformTaskDispatcher(harness["store"], harness["session_factory"])(child_ref, record, None)
    child_state = harness["store"].get_task_state(child_ref)
    assert child_state.lifecycle_state.value == "failed"
    assert (child_state.extra or {}).get("result", {}).get("error_category") == "parent_cancelled"
    assert llm_stub.child_asks == 0


async def test_parent_deadline_converges_the_pair(harness, llm_stub) -> None:
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    target_id = await _add_agent(harness, "worker-agent")
    deadline = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    parent = await _submit_parent(harness, _declaration_of(harness, target_id), controller_extra={"deadline": deadline})
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    state = harness["store"].get_task_state(parent.task_ref)
    assert ((state.extra or {}).get("wait") or {})["wait_expires_at"] == deadline
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    expired = AgentChildRunReconciler(
        harness["store"], harness["session_factory"], clock=lambda: datetime.now(UTC) + timedelta(seconds=60)
    )
    assert await expired.converge_once() == 1
    # A not-yet-due pass leaves the pair alone.
    not_due = AgentChildRunReconciler(harness["store"], harness["session_factory"])
    assert await not_due.converge_once() == 0
    parent_state = harness["store"].get_task_state(parent.task_ref)
    assert parent_state.lifecycle_state.value == "failed"
    assert (parent_state.extra or {}).get("result", {}).get("error_category") == "agent_child_deadline"
    # The still-queued child received the durable cancel request and is
    # cancelled outright (nothing dispatched may run for a converged parent).
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "cancelled"
    assert llm_stub.child_asks == 0


async def test_task_detail_surfaces_the_parent_child_facts(harness, llm_stub) -> None:
    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    async with harness["session_factory"]() as db:
        detail = await _service_of(harness, db).get_task_detail(WS, uuid.UUID(parent.task_ref.id))
    assert detail["agent_children"]["accepted_calls"] == 1
    assert detail["agent_children"]["pending"]["alias"] == "worker"
    assert detail["agent_children"]["pending"]["consumed"] is False
    assert detail["wait"]["wake_kind"] == "agent_child"

    async with harness["session_factory"]() as db:
        child_detail = await _service_of(harness, db).get_task_detail(WS, uuid.UUID(child_ref.id))
    assert child_detail["agent_child_parent"]["alias"] == "worker"
    assert child_detail["agent_child_parent"]["parent_task_ref"]["id"] == parent.task_ref.id


async def test_parent_policy_intersection_refuses_denied_tools(harness, llm_stub, monkeypatch) -> None:
    """A tool the parent's own policy denies is not a delegable grant."""

    import hecate.core.composition.entry_assembly as entry_assembly
    from hecate.models.tool_policy import ToolPolicyRuleModel

    definitions = [
        {
            "type": "function",
            "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}},
        }
        for name in ("query_inventory", "dangerous_tool")
    ]

    async def _load_tools(db, tool_names, *, workspace_id=None):
        return definitions

    monkeypatch.setattr(entry_assembly, "load_agent_tools", _load_tools)
    async with harness["session_factory"]() as db:
        db.add(
            ToolPolicyRuleModel(
                workspace_id=WS,
                agent_id=None,
                tool_pattern="dangerous_tool",
                action="deny",
                priority=10,
                arg_conditions={},
            )
        )
        await db.commit()

    target_id = await _add_agent(harness, "worker-agent", tools=["query_inventory", "dangerous_tool"])
    declaration = _declaration_of(harness, target_id)
    declaration["targets"][0]["allowed_tools"] = ["query_inventory", "dangerous_tool"]
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    await worker.dispatch_once(parent.task_ref)
    # The acceptance refused the denied tool as data; the loop finished
    # without a child and without parking.
    state = harness["store"].get_task_state(parent.task_ref)
    assert state.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert (state.extra or {}).get("wait") is None
    assert _children_of(harness, parent.task_ref) == []
    assert llm_stub.child_asks == 0


async def test_budget_slice_bounds_the_child_and_accumulates_on_the_parent(harness, llm_stub) -> None:
    """The declared slice reserves durably, settles on the facts, never resets."""

    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 10000
    declaration["targets"][0]["budget_units"] = 5000
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    wait = (harness["store"].get_task_state(parent.task_ref).extra or {}).get("wait") or {}
    assert wait["contract_ref"]["budget_units"] == 5000
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    assert await worker.dispatch_once(child_ref) is True
    child_record = harness["store"].get_task_state(child_ref)
    assert child_record.lifecycle_state.value == "succeeded"
    child_budget = (child_record.extra or {}).get("result", {}).get("budget")
    assert child_budget is not None and child_budget["allocated"] == 5000
    assert 0 < child_budget["used"] < 5000, child_budget  # settled below the slice

    assert await worker.dispatch_once(parent.task_ref) is True
    final = harness["store"].get_task_state(parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    assert final.extra["agent_child_budget_used"] == child_budget["used"]
    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref)
    assert revisions[0]["content"]["budget"] == child_budget


async def test_task_detail_projects_the_recorded_child_budget_and_reads_without_writes(harness, llm_stub) -> None:
    """The detail GET whitelists the parent's cumulative spend; absence and
    zero stay distinct, and a repeat read moves nothing."""
    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 10000
    declaration["targets"][0]["budget_units"] = 5000
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    # Accepted but not yet consumed: the counter was never recorded.
    async with harness["session_factory"]() as db:
        pending = await _service_of(harness, db).get_task_detail(WS, uuid.UUID(parent.task_ref.id))
    assert "agent_child_budget_used" not in pending

    assert await worker.dispatch_once(child_ref) is True
    assert await worker.dispatch_once(parent.task_ref) is True
    state = harness["store"].get_task_state(parent.task_ref)
    recorded = (state.extra or {}).get("agent_child_budget_used")
    assert isinstance(recorded, int) and recorded > 0

    async with harness["session_factory"]() as db:
        first = await _service_of(harness, db).get_task_detail(WS, uuid.UUID(parent.task_ref.id))
    assert first["agent_child_budget_used"] == recorded
    # The spend accumulates on the parent only; the child's own detail
    # carries no such counter.
    async with harness["session_factory"]() as db:
        child_detail = await _service_of(harness, db).get_task_detail(WS, uuid.UUID(child_ref.id))
    assert "agent_child_budget_used" not in child_detail
    assert "workflow_budget_used" not in first

    async with harness["session_factory"]() as db:
        second = await _service_of(harness, db).get_task_detail(WS, uuid.UUID(parent.task_ref.id))
    assert second == first
    after = harness["store"].get_task_state(parent.task_ref)
    assert (after.extra or {}).get("agent_child_budget_used") == recorded
    assert after.revision == state.revision


async def test_restart_in_an_independent_process_recovers_the_lost_callback(harness, llm_stub, monkeypatch) -> None:
    """A fresh interpreter converges the same parent-child facts, no duplicates."""

    target_id = await _add_agent(harness, "worker-agent")
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    async def silent_callback(self, db, context, outcome, *, retry_errors=False):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", silent_callback
    )
    assert await worker.dispatch_once(child_ref) is True
    assert harness["store"].get_task_state(child_ref).lifecycle_state.value == "succeeded"

    completed = await _run_child_process(
        harness, "tests.test_execution._agent_child_process", parent.task_ref.id, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["backend"] == ("postgresql" if harness["pg"] else "sqlite")
    assert report["recovered"] == 1
    assert report["state_after_recovery"] == "queued"
    assert report["dispatched"] is True
    assert report["state"] == "succeeded"
    assert report["content"] == json.dumps({"answer": "recovered: " + json.dumps({"answer": "child done"})})
    assert report["revisions"] == ["agent_child_result"]
    assert len(report["child_call_ids"]) == 1, "recovery must not duplicate the child"

    # The recovered fact is durable across the process boundary: the parent
    # stays terminal and its wait stays consumed for the outer process too.
    final = harness["store"].get_task_state(parent.task_ref)
    assert final.lifecycle_state.value == "succeeded"
    assert ((final.extra or {}).get("wait") or {}).get("consumed") is True


async def test_interrupted_child_recovers_without_repeating_settled_effects(harness, llm_stub, monkeypatch) -> None:
    """A crash after a settled action replays the decision, never the effect."""

    from hecate_runtime.accepted_action_batch import AcceptedActionBatch

    import hecate.core.composition.entry_assembly as entry_assembly
    from hecate.tools.tool.builtin import BuiltInToolExecutor

    async def _load_tools(db, tool_names, *, workspace_id=None):
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

    tool_runs = 0
    real_execute = BuiltInToolExecutor.execute

    async def counting_execute(self, name, args, context=None):
        nonlocal tool_runs
        if name != "web_search":
            return await real_execute(self, name, args, context)
        tool_runs += 1
        return {"results": ["found"]}

    monkeypatch.setattr(BuiltInToolExecutor, "execute", counting_execute)

    crashed = 0
    armed = False
    real_batch_run = AcceptedActionBatch.run

    async def crash_after_settle(self, **kwargs):
        nonlocal crashed
        result = await real_batch_run(self, **kwargs)
        if armed and crashed == 0:
            crashed += 1
            raise OSError("injected process crash after the action settled")
        return result

    monkeypatch.setattr(AcceptedActionBatch, "run", crash_after_settle)

    original_stream = llm_stub.chat_stream

    async def child_tool_stream(*, messages, model=None, tools=None, **kwargs):
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        if "delegate_agent" in named:
            async for chunk in original_stream(messages=messages, model=model, tools=tools, **kwargs):
                yield chunk
            return
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        llm_stub.child_asks += 1
        if not tool_messages:
            yield {
                "content": "",
                "tool_calls": [_Delta(0, "call-1", "web_search", json.dumps({"query": "stock"}))],
            }
            return
        yield {"content": json.dumps({"answer": "child done"}), "tool_calls": None}

    monkeypatch.setattr(llm_stub, "chat_stream", child_tool_stream)

    target_id = await _add_agent(harness, "worker-agent", tools=["web_search"])
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    armed = True  # the crash targets the child's first settled batch only
    await worker.dispatch_once(child_ref)
    record = harness["store"].get_task_state(child_ref)
    if record.lifecycle_state.value == "queued" and record.extra.get("next_dispatch_at"):
        await asyncio.to_thread(
            harness["store"].apply_task_state,
            child_ref,
            TaskLifecycleState.QUEUED,
            extra_update={"next_dispatch_at": None},
        )
    ok = await worker.dispatch_once(child_ref)
    actions = harness["store"].list_run_actions(harness["store"].run_for_task(child_ref))
    print(
        "CHILD ACTIONS:",
        [
            (
                a.get("action_key", "")[:20],
                a.get("intent", {}).get("action_name"),
                a.get("state"),
                str(a.get("last_outcome", {}).get("result_content") or a.get("result_payload"))[:120],
            )
            for a in actions
        ],
    )
    assert ok is True, str(record.extra)

    child_record = harness["store"].get_task_state(child_ref)
    assert child_record.lifecycle_state.value == "succeeded", child_record.extra
    assert llm_stub.child_asks == 2, "the committed turn must replay without a model call"
    assert tool_runs == 1, "the settled effect must never re-execute"
    assert await worker.dispatch_once(parent.task_ref) is True
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "succeeded"


async def test_running_child_stops_for_a_parent_cancel(harness, llm_stub, monkeypatch) -> None:
    """A child mid-execution observes the parent's cancel before its next turn."""

    import hecate.core.composition.entry_assembly as entry_assembly
    from hecate.contracts.execution.durable import CommandState, ControlCommandKind
    from hecate.tools.tool.builtin import BuiltInToolExecutor

    async def _load_tools(db, tool_names, *, workspace_id=None):
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

    release = asyncio.Event()
    entered = asyncio.Event()
    tool_runs = 0
    real_execute = BuiltInToolExecutor.execute

    async def gated_execute(self, name, args, context=None):
        nonlocal tool_runs
        if name != "web_search":
            return await real_execute(self, name, args, context)
        tool_runs += 1
        entered.set()
        await release.wait()
        return {"results": ["found"]}

    monkeypatch.setattr(BuiltInToolExecutor, "execute", gated_execute)

    original_stream = llm_stub.chat_stream
    asks_after_release = 0

    async def child_tool_stream(*, messages, model=None, tools=None, **kwargs):
        nonlocal asks_after_release
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        if "delegate_agent" in named:
            async for chunk in original_stream(messages=messages, model=model, tools=tools, **kwargs):
                yield chunk
            return
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        llm_stub.child_asks += 1
        if release.is_set():
            asks_after_release += 1
        if not tool_messages:
            yield {
                "content": "",
                "tool_calls": [_Delta(0, "call-1", "web_search", json.dumps({"query": "stock"}))],
            }
            return
        yield {"content": json.dumps({"answer": "child done"}), "tool_calls": None}

    monkeypatch.setattr(llm_stub, "chat_stream", child_tool_stream)

    target_id = await _add_agent(harness, "worker-agent", tools=["web_search"])
    parent = await _submit_parent(harness, _declaration_of(harness, target_id))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    driver = asyncio.create_task(worker.dispatch_once(child_ref))
    await asyncio.wait_for(entered.wait(), timeout=30)
    async with harness["session_factory"]() as db:
        issued = await _service_of(harness, db).issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
        )
    assert issued.record.state is not CommandState.REJECTED
    release.set()
    assert await driver is True

    child_record = harness["store"].get_task_state(child_ref)
    assert child_record.lifecycle_state.value == "cancelled", child_record.extra
    assert llm_stub.child_asks == 1 and asks_after_release == 0
    assert tool_runs == 1
    assert harness["store"].get_task_state(parent.task_ref).lifecycle_state.value == "cancelled"


async def test_slice_usage_persists_across_a_child_dispatch_crash(harness, llm_stub, monkeypatch) -> None:
    """A crashed child dispatch re-seeds its slice from the durable block."""

    from hecate_runtime.accepted_action_batch import AcceptedActionBatch

    import hecate.core.composition.entry_assembly as entry_assembly

    async def _load_tools(db, tool_names, *, workspace_id=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "search",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]

    monkeypatch.setattr(entry_assembly, "load_agent_tools", _load_tools)

    real_batch_run = AcceptedActionBatch.run
    crashed = 0
    armed = False

    async def crash_after_first_turn(self, **kwargs):
        nonlocal crashed
        result = await real_batch_run(self, **kwargs)
        if armed and crashed == 0:
            crashed += 1
            raise OSError("injected child crash after the first turn")
        return result

    monkeypatch.setattr(AcceptedActionBatch, "run", crash_after_first_turn)

    import hecate.core.composition.entry_assembly as entry_assembly
    from hecate.tools.tool.builtin import BuiltInToolExecutor

    async def _load_tools(db, tool_names, *, workspace_id=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "search",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ]

    monkeypatch.setattr(entry_assembly, "load_agent_tools", _load_tools)
    tool_runs = 0
    real_execute = BuiltInToolExecutor.execute

    async def counting_execute(self, name, args, context=None):
        nonlocal tool_runs
        if name != "web_search":
            return await real_execute(self, name, args, context)
        tool_runs += 1
        return {"results": ["found"]}

    monkeypatch.setattr(BuiltInToolExecutor, "execute", counting_execute)

    original_stream = llm_stub.chat_stream

    async def child_stream(*, messages, model=None, tools=None, **kwargs):
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        if "delegate_agent" in named:
            async for chunk in original_stream(messages=messages, model=model, tools=tools, **kwargs):
                yield chunk
            return
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        llm_stub.child_asks += 1
        if not tool_messages:
            yield {
                "content": "",
                "tool_calls": [_Delta(0, "call-1", "web_search", json.dumps({"query": "stock"}))],
            }
            return
        yield {"content": json.dumps({"answer": "child done"}), "tool_calls": None}

    monkeypatch.setattr(llm_stub, "chat_stream", child_stream)

    target_id = await _add_agent(harness, "worker-agent", tools=["web_search"])
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 50000
    declaration["targets"][0]["budget_units"] = 20000
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    armed = True
    await worker.dispatch_once(child_ref)  # dies after the first committed turn
    armed = False
    record = harness["store"].get_task_state(child_ref)
    if record.lifecycle_state.value == "queued" and record.extra.get("next_dispatch_at"):
        await asyncio.to_thread(
            harness["store"].apply_task_state,
            child_ref,
            TaskLifecycleState.QUEUED,
            extra_update={"next_dispatch_at": None},
        )
    assert await worker.dispatch_once(child_ref) is True
    child_record = harness["store"].get_task_state(child_ref)
    print("CHILD RESULT:", str((child_record.extra or {}).get("result"))[:300])
    print("CHILD SLICE:", str((child_record.extra or {}).get("budget_slice"))[:300])
    actions = harness["store"].list_run_actions(harness["store"].run_for_task(child_ref))
    print(
        "CHILD ACTIONS:",
        [
            (
                a.get("intent", {}).get("action_name"),
                a.get("state"),
                str(a.get("last_outcome", {}).get("result_content"))[:100],
                a.get("pending_reconciliation"),
            )
            for a in actions
        ],
    )
    print("tool_runs:", tool_runs, "child_asks:", llm_stub.child_asks)
    assert child_record.lifecycle_state.value == "succeeded", child_record.extra

    # The durable slice block carries BOTH dispatches' model usage: the
    # first attempt's settled turn and the recovery's turns. A fresh
    # per-dispatch in-memory ledger would lose the first attempt.
    from hecate.execution.agent_child import derive_slice_usage

    block = (child_record.extra or {}).get("budget_slice")
    assert block is not None, "the slice state must be durable on the child row"
    assert block["allocated"] == 20000
    assert len(block["entries"]) == 2, "both dispatches' model usage persists"
    derived = derive_slice_usage(block, allocated=20000)
    assert derived["unknown_count"] == 0
    assert tool_runs == 1, "the settled effect never re-executes"
    assert llm_stub.child_asks == 2, "the committed turn replays without a model call"
    # first attempt's settled turn and the recovery's turns. A fresh
    # per-dispatch in-memory ledger would lose the first attempt.
    from hecate.execution.agent_child import derive_slice_usage

    block = (child_record.extra or {}).get("budget_slice")
    assert block is not None, "the slice state must be durable on the child row"
    assert block["allocated"] == 20000
    assert len(block["entries"]) == 2, "both dispatches' model usage persists"
    derived = derive_slice_usage(block, allocated=20000)
    assert derived["unknown_count"] == 0
    assert tool_runs == 1, "the settled effect never re-executes"
    assert llm_stub.child_asks == 2, "the committed turn replays without a model call"


async def test_unknown_hold_survives_restart_and_refuses_before_the_next_model_request(
    harness, llm_stub, monkeypatch
) -> None:
    """A model attempt that died mid-stream keeps its hold; the retry is refused."""

    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 20000
    declaration["targets"][0]["budget_units"] = 5000  # one estimate fits, two do not
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    original_stream = llm_stub.chat_stream

    async def dying_child_stream(*, messages, model=None, tools=None, **kwargs):
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        if "delegate_agent" in named:
            async for chunk in original_stream(messages=messages, model=model, tools=tools, **kwargs):
                yield chunk
            return
        llm_stub.child_asks += 1
        yield {"content": "partial"}
        raise RuntimeError("process died mid-stream")

    monkeypatch.setattr(llm_stub, "chat_stream", dying_child_stream)

    await worker.dispatch_once(child_ref)  # the model attempt dies: unknown hold
    record = harness["store"].get_task_state(child_ref)
    child_state_before = record.lifecycle_state.value
    if child_state_before == "queued" and record.extra.get("next_dispatch_at"):
        await asyncio.to_thread(
            harness["store"].apply_task_state,
            child_ref,
            TaskLifecycleState.QUEUED,
            extra_update={"next_dispatch_at": None},
        )

    monkeypatch.setattr(llm_stub, "chat_stream", original_stream)
    asks_before = llm_stub.child_asks
    await worker.dispatch_once(child_ref)
    record = harness["store"].get_task_state(child_ref)

    # The retry's model request was refused BEFORE reaching the model: the
    # unknown hold from the dead attempt is still charged, so the slice
    # cannot fund a second full-call estimate.
    assert llm_stub.child_asks == asks_before, "the retry must be refused before a new model request"
    from hecate.execution.agent_child import derive_slice_usage

    block = (record.extra or {}).get("budget_slice")
    derived = derive_slice_usage(block, allocated=5000)
    assert derived["unknown_count"] >= 1, derived
    assert derived["used"] > 0
    # Every retry is refused before the model again; the worker's bounded
    # budget parks the child for reconciliation.
    for _ in range(6):
        record = harness["store"].get_task_state(child_ref)
        if record.lifecycle_state.value in ("failed", "reconciliation_required"):
            break
        if record.extra.get("next_dispatch_at"):
            await asyncio.to_thread(
                harness["store"].apply_task_state,
                child_ref,
                TaskLifecycleState.QUEUED,
                extra_update={"next_dispatch_at": None},
            )
        await worker.dispatch_once(child_ref)
    record = harness["store"].get_task_state(child_ref)
    assert record.lifecycle_state.value == "reconciliation_required", record.extra
    assert llm_stub.child_asks == asks_before, "no retry ever reached the model"


async def test_reservation_released_when_the_child_submission_is_refused(harness) -> None:
    """A validation refusal after the reservation releases the unexecuted hold."""

    from hecate.execution.agent_child import AgentChildDelegateTool, agent_child_policy
    from hecate.models.organization import OrganizationModel
    from hecate.models.workspace import WorkspaceModel

    other_ws = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
    other_org = uuid.UUID("00000000-0000-0000-0000-0000000000e2")
    async with harness["session_factory"]() as db:
        db.add(OrganizationModel(id=other_org, name="other-rel", slug=f"org-rel-{other_org.hex[:8]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=other_ws, org_id=other_org, name="other-ws", slug=f"ws-{other_ws.hex}"))
        await db.commit()
    foreign_target = await _add_agent(harness, "foreign-worker")
    async with harness["session_factory"]() as db:
        from hecate.models.agent import AgentModel

        agent = await db.get(AgentModel, foreign_target)
        agent.workspace_id = other_ws
        await db.commit()

    declaration = _declaration_of(harness, foreign_target)
    declaration["budget_units"] = 10000
    declaration["targets"][0]["budget_units"] = 5000
    policy = agent_child_policy(json.loads(json.dumps(declaration)))
    tool = AgentChildDelegateTool(
        policy=policy,
        parent_ref=task_ref_of(uuid.uuid4()),
        parent_goal="x",
        initiator_user_id=str(OWNER),
        parent_agent_id=harness["agent_id"],
        workspace_id=str(WS),
        session_factory=harness["session_factory"],
        store=harness["store"],
        engine_backed=True,
    )
    result = await tool.execute(
        "delegate_agent", {"alias": "worker", "input": {"task": "x"}}, {"invocation_id": "call-rel-1"}
    )
    assert result["status"] == "agent_child_refused"
    from hecate.models.quota import QuotaReservationModel

    async with harness["session_factory"]() as db:
        from sqlalchemy import select

        rows = (
            (
                await db.execute(
                    select(QuotaReservationModel).where(QuotaReservationModel.invocation_key.like("%call-rel-1"))
                )
            )
            .scalars()
            .all()
        )
    released = [row for row in rows if row.released]
    assert rows, "the reservation fact must exist"
    assert released and all(row.released for row in rows), "the unexecuted hold must be released"


async def test_callback_settlement_is_idempotent_under_redelivery(harness, llm_stub, monkeypatch) -> None:
    """A crash between the budget settlement and the wake converges without double charge."""

    from hecate.models.quota import QuotaReservationModel

    target_id = await _add_agent(harness, "worker-agent")
    declaration = _declaration_of(harness, target_id)
    declaration["budget_units"] = 50000
    declaration["targets"][0]["budget_units"] = 20000
    parent = await _submit_parent(harness, declaration)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = _children_of(harness, parent.task_ref)[0]

    real_apply = harness["store"].apply_task_state
    wake_failures = 0

    def flaky_wake(task_ref, target, **kwargs):
        nonlocal wake_failures
        if (
            target is TaskLifecycleState.QUEUED
            and (kwargs.get("extra_update") or {}).get("wait", {}).get("wake_kind") == "agent_child"
        ):
            wake_failures += 1
            if wake_failures == 1:
                raise OSError("injected crash between settlement and wake")
        return real_apply(task_ref, target, **kwargs)

    monkeypatch.setattr(harness["store"], "apply_task_state", flaky_wake)

    assert await worker.dispatch_once(child_ref) is True  # settles, then the wake crashes
    assert wake_failures >= 1

    # The child's terminal callback retries through the named reconciler
    # (the same deterministic callback): the settlement is idempotent and
    # the wake converges exactly once.
    from hecate.execution.agent_child_wake import AgentChildRunReconciler

    reconciler = AgentChildRunReconciler(harness["store"], harness["session_factory"])
    assert await reconciler.converge_once() == 1
    record = await asyncio.to_thread(harness["store"].get_task_state, parent.task_ref)
    assert record.lifecycle_state.value == "queued", record.extra

    async with harness["session_factory"]() as db:
        from sqlalchemy import select

        rows = (await db.execute(select(QuotaReservationModel))).scalars().all()
    child_reservations = [row for row in rows if row.invocation_key.startswith("agent-child:")]
    assert child_reservations, "the call reservation must be settled from the durable slice"
    assert all(row.settled for row in child_reservations), "the hold settled exactly once, from the trusted slice"
    assert all(row.settled_units == child_reservations[0].settled_units for row in child_reservations), (
        "no double charge across redeliveries"
    )

    assert await worker.dispatch_once(parent.task_ref) is True
    final = harness["store"].get_task_state(parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    assert final.extra["agent_child_budget_used"] == round(child_reservations[0].settled_units / 0.00001)
