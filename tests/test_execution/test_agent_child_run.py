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
from hecate.execution.task_control import TaskControlService
from tests.test_execution.conftest import HARNESS_ORG as ORG
from tests.test_execution.conftest import HARNESS_OWNER as OWNER
from tests.test_execution.conftest import HARNESS_WS as WS

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


async def test_restart_in_an_independent_process_recovers_the_lost_callback(harness, llm_stub, monkeypatch) -> None:
    """A fresh interpreter converges the same parent-child facts, no duplicates."""

    import subprocess
    import sys
    from pathlib import Path

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

    command = [
        sys.executable,
        "-m",
        "tests.test_execution._agent_child_process",
        str(harness["store"].engine.url),
        parent.task_ref.id,
    ]
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
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
