"""Platform-native continuation acceptance (step6d).

Real chain, no ``_execute`` monkeypatching: ``PlatformTaskDispatcher`` →
``EntryExecutionService`` → ``WorkflowExecutionService`` → Pregel. An
interrupted attempt (first dispatch parked mid-flight = the hung-process
equivalent, or cancelled and settled) is re-driven through the worker's
stale-running recovery and MUST resume on the SAME run and engine
session when the resumption gate passes — decided actions replay from
the ledger with a zero-duplicate business call count — and MUST
reconcile conservatively when the gate refuses (definition-digest
drift) or the ledger holds a claimed-undecided action (external write
succeeded, receipt lost).

Provider boundary is stubbed only (``hecate_llm.service.llm_service``),
the G3 precedent; tools run through the real gateway with a stub
builtin executor.

Concurrency tier: the hang/resume and receipt-loss scenarios need a real
multi-connection database (SQLite's single-writer semantics serialize
them away), so they run against ``HECATE_STEP6_POSTGRES_URL`` — CI's
Linux job or a local Docker PostgreSQL. Locally they report an honest
skip, never a pass.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from hecate.models.agent_version import AgentVersionModel
from hecate.models.run import RunModel
from hecate.models.tool import ToolModel
from tests.test_execution.conftest import HARNESS_OWNER as OWNER
from tests.test_execution.conftest import HARNESS_POSTGRES_URL
from tests.test_execution.conftest import HARNESS_WS as WS

TOOL_NAME = "stub_calculator"
TOOL_ARGS = '{"a": 2, "b": 3}'

_PG_SKIP = pytest.mark.skipif(
    not HARNESS_POSTGRES_URL,
    reason="needs PostgreSQL for hang/resume concurrency (HECATE_STEP6_POSTGRES_URL)",
)


class ScriptedLLM:
    """Provider-boundary double: tool call first, final answer after."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.gate_blocked = False
        self.hold_at_call = 0  # block the Nth call (crash simulation window)

    def _saw_tool(self, messages: list[dict]) -> bool:
        return any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)

    def _stream_delta(self, call_id: str = "c1"):
        # LiteLLM-style streaming delta: the adapter reads attributes.
        return SimpleNamespace(
            index=0,
            id=call_id,
            type="function",
            function=SimpleNamespace(name=TOOL_NAME, arguments=TOOL_ARGS),
        )

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        self.calls.append([dict(m) for m in messages])
        if self._saw_tool(messages):
            return SimpleNamespace(
                content="final answer",
                model=model or "stub-model",
                tool_calls=None,
                finish_reason="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        return SimpleNamespace(
            content="",
            model=model or "stub-model",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": TOOL_NAME, "arguments": TOOL_ARGS}}],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        self.calls.append(list(messages))
        if self.hold_at_call and len(self.calls) == self.hold_at_call:
            self.gate_blocked = True
            await self.gate.wait()
        if self._saw_tool(messages):
            for token in ("final", " answer"):
                yield {"content": token, "tool_calls": None}
            yield {"content": "", "finish_reason": "stop", "tool_calls": None}
        else:
            yield {"content": "", "tool_calls": [self._stream_delta()]}
            yield {"content": None, "tool_calls": None}


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, args, context=None):
        self.calls.append((name, dict(args or {})))
        return "5"


@pytest.fixture
def llm_stub(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    stub = ScriptedLLM()
    import hecate_llm.service as llm_module

    monkeypatch.setattr(llm_module, "llm_service", stub)
    return stub


@pytest.fixture
def tool_executor(monkeypatch: pytest.MonkeyPatch) -> RecordingExecutor:
    """Swap the builtin executor behind the real ToolRegistry."""
    from hecate.tools.tool.registry import ToolRegistry

    executor = RecordingExecutor()

    def _build(db, skill_ref_manifest=None, *, workspace_id=None):
        registry = ToolRegistry(db=db, builtin_executor=executor, workspace_id=workspace_id)
        registry._builtin_names = {TOOL_NAME}
        return registry

    import hecate.core.composition.entry_assembly as entry_assembly

    monkeypatch.setattr(entry_assembly, "build_tool_registry", _build)
    return executor


@pytest.fixture
async def tooled_agent(harness):
    """Give the harness agent a second version with a registered tool."""

    async with harness["session_factory"]() as db:
        db.add(
            ToolModel(
                name=TOOL_NAME,
                description="Deterministic stub calculator",
                parameters={
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                },
                source="builtin",
                workspace_id=WS,
            )
        )
        agent_id = harness["agent_id"]
        version = AgentVersionModel(
            agent_id=agent_id,
            version=2,
            config_snapshot={"model": "stub-model", "tools": [TOOL_NAME]},
            content_hash="d" * 64,
        )
        db.add(version)
        await db.commit()

    from hecate.models.agent_deployment import AgentDeploymentModel

    async with harness["session_factory"]() as db:
        deployment = (
            (await db.execute(select(AgentDeploymentModel).where(AgentDeploymentModel.agent_id == agent_id)))
            .scalars()
            .first()
        )
        deployment.agent_version_id = version.id
        await db.commit()

    # Fail fast with a clear message when the tool row is not resolvable
    # through the same lookup the dispatcher uses.
    from hecate.core.composition.entry_assembly import load_agent_tools

    async with harness["session_factory"]() as db:
        resolved = await load_agent_tools(db, [TOOL_NAME], workspace_id=WS)
    assert resolved, f"tool {TOOL_NAME!r} not resolvable for workspace {WS} — check ToolModel row/lookup"


def _platform_dispatcher(harness):
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    return PlatformTaskDispatcher(harness["store"], harness["session_factory"])


async def _submit(harness, goal: str):
    from hecate.execution.task_control import TaskControlService

    async with harness["session_factory"]() as db:
        return await TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        ).submit(
            workspace_id=WS,
            user_id=OWNER,
            goal=goal,
            agent_id=harness["agent_id"],
            input={"messages": [{"role": "user", "content": "go"}]},
        )


async def _run_rows(harness, task_id: uuid.UUID) -> list[RunModel]:
    async with harness["session_factory"]() as db:
        return list(
            (await db.execute(select(RunModel).where(RunModel.task_id == task_id).order_by(RunModel.created_at)))
            .scalars()
            .all()
        )


async def _crash_first_dispatch(harness, llm_stub, task_ref, monkeypatch, *, park: bool):
    """Dispatch for real, then interrupt mid-flight (process-death equivalent).

    The tool has executed (business call recorded, ledger action decided)
    and the second model call is parked on the gate when the interruption
    lands — no terminal fact is written, the attempt stays RUNNING.

    ``park=True`` leaves the dispatch task hung (a frozen process: its
    connections stay open); the caller releases it after assertions and
    the resumed dispatch's fencing rejects its late writes.
    ``park=False`` cancels and settles (a crashed process that cleaned
    up its connections).
    """

    from hecate_durable.worker import DurableWorker

    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())
    worker = DurableWorker(harness["store"], _platform_dispatcher(harness), leases=harness["store"].leases)
    llm_stub.hold_at_call = 2
    llm_stub.gate.clear()
    dispatch = asyncio.create_task(worker.dispatch_once(task_ref))
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        if len(llm_stub.calls) >= 2 and llm_stub.gate_blocked:
            break
        await asyncio.sleep(0.05)
    else:
        state = harness["store"].get_task_state(task_ref)
        llm_stub.gate.set()
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(dispatch, 30)
        raise AssertionError(
            f"dispatch never reached the second model call; calls={len(llm_stub.calls)} "
            f"roles={[[str(m.get('role')) for m in c] for c in llm_stub.calls]} "
            f"state={state.lifecycle_state} extra={state.extra}"
        )
    llm_stub.hold_at_call = 0
    if park:
        return dispatch
    dispatch.cancel()
    with suppress(asyncio.CancelledError):
        await dispatch
    # Let the cancelled dispatch's connections settle before re-driving.
    await asyncio.sleep(0.5)
    return None


async def _requeue_and_assert_resumed(harness, llm_stub, tool_executor, task_ref, engine_session: str):
    """Expire the dead lease, re-drive via stale-running recovery, assert
    the SAME run/engine session continued natively to success."""

    harness["clock"].advance(300.0)
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(harness["store"], _platform_dispatcher(harness), leases=harness["store"].leases)
    dispatched = await worker.reconcile()

    state = harness["store"].get_task_state(task_ref)
    assert dispatched >= 1, (
        f"recovery did not dispatch; state={state.lifecycle_state} extra={state.extra} "
        f"llm={len(llm_stub.calls)} tool={len(tool_executor.calls)}"
    )
    assert state.lifecycle_state.value == "succeeded", state.extra
    runs = await _run_rows(harness, uuid.UUID(task_ref.id))
    assert len(runs) == 1, "native continuation must not mint a new attempt"
    assert runs[0].backend_ref["id"] == engine_session, "same engine session"


@_PG_SKIP
async def test_interrupted_attempt_resumes_native_same_run(
    harness, llm_stub, tool_executor, tooled_agent, monkeypatch
) -> None:
    result = await _submit(harness, "native continuation")
    task_ref = result.task_ref
    parked = await _crash_first_dispatch(harness, llm_stub, task_ref, monkeypatch, park=True)

    # Crash facts: no terminal, business write exactly once, digest recorded.
    state = harness["store"].get_task_state(task_ref)
    assert state.lifecycle_state.value in ("running", "queued"), state.extra
    assert len(llm_stub.calls) >= 2, f"roles={[[str(m.get('role')) for m in c] for c in llm_stub.calls]}"
    assert len(tool_executor.calls) == 1, f"roles={[[str(m.get('role')) for m in c] for c in llm_stub.calls]}"
    runs = await _run_rows(harness, uuid.UUID(task_ref.id))
    assert len(runs) == 1
    recorded = (runs[0].execution_snapshot or {}).get("definition_digest")
    assert recorded, "first dispatch must freeze the resolved definition digest"
    engine_session = runs[0].backend_ref["id"]
    ledger_after_crash = harness["store"].list_run_actions(
        __import__("hecate_durable").contracts.references.run_ref("hecate", str(runs[0].id))
    )
    crash_ledger = [
        (
            (a.get("intent") or {}).get("action_key"),
            (a.get("last_outcome") or {}).get("outcome"),
        )
        for a in ledger_after_crash
    ]
    assert crash_ledger and crash_ledger[0][1] == "succeeded", f"crash ledger={crash_ledger}"

    # The gate stays closed through the resume: the parked writer must not
    # append to the shared session log while the resumed dispatch restores —
    # projection equivalence compares live state against a full-log fold, so
    # a concurrent late append would trip the fail-closed invariant. The
    # resume's model call is a NEW call index, so it proceeds ungated.
    await _requeue_and_assert_resumed(harness, llm_stub, tool_executor, task_ref, engine_session)
    assert len(tool_executor.calls) == 1, "decided action replays, never re-executes"
    assert len(llm_stub.calls) == 3

    # Release the hung dispatch last: its continuation completes but its
    # terminal write is fenced away by the resumed dispatch's ownership —
    # no duplicate business effect, no state rewrite.
    llm_stub.gate.set()
    with suppress(asyncio.CancelledError, asyncio.TimeoutError):
        await asyncio.wait_for(parked, 60)
    assert len(tool_executor.calls) == 1, "late continuation must not redo the write"


@_PG_SKIP
async def test_receipt_loss_stops_conservatively_at_resume(
    harness, llm_stub, tool_executor, tooled_agent, monkeypatch
) -> None:
    result = await _submit(harness, "receipt loss")
    task_ref = result.task_ref
    await _crash_first_dispatch(harness, llm_stub, task_ref, monkeypatch, park=False)

    # Simulate "external write succeeded, receipt lost": drop the decided
    # outcome row, leaving the claim without an established result.
    engine = harness["store"].engine

    def _delete_outcome(sync_conn):
        from sqlalchemy import text

        sync_conn.execute(text("DELETE FROM durable_action_outcome"))

    with engine.begin() as conn:
        conn.execute(_sql_delete())

    llm_stub.gate.set()
    harness["clock"].advance(300.0)
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(harness["store"], _platform_dispatcher(harness), leases=harness["store"].leases)
    await worker.reconcile()

    state = harness["store"].get_task_state(task_ref)
    assert state.lifecycle_state.value == "reconciliation_required", state.extra
    assert len(tool_executor.calls) == 1, "the undecided action is never redone"
    runs = await _run_rows(harness, uuid.UUID(task_ref.id))
    assert len(runs) == 1


def _sql_delete():
    from sqlalchemy import text

    return text("DELETE FROM durable_action_outcome")


@_PG_SKIP
async def test_definition_digest_drift_refuses_resume(
    harness, llm_stub, tool_executor, tooled_agent, monkeypatch
) -> None:
    result = await _submit(harness, "digest drift")
    task_ref = result.task_ref
    await _crash_first_dispatch(harness, llm_stub, task_ref, monkeypatch, park=False)

    # Mutate the recorded digest: the interrupted session would continue
    # under a different definition than it started with.
    async with harness["session_factory"]() as db:
        run = (await db.execute(select(RunModel).where(RunModel.task_id == uuid.UUID(task_ref.id)))).scalars().first()
        run.execution_snapshot = {**(run.execution_snapshot or {}), "definition_digest": "drifted-digest"}
        await db.commit()

    llm_stub.gate.set()
    calls_before = len(llm_stub.calls)
    harness["clock"].advance(300.0)
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(harness["store"], _platform_dispatcher(harness), leases=harness["store"].leases)
    await worker.reconcile()

    state = harness["store"].get_task_state(task_ref)
    assert state.lifecycle_state.value == "reconciliation_required", state.extra
    assert "drifted" in (state.extra or {}).get("reconciliation_reason", "")
    assert len(llm_stub.calls) == calls_before, "drifted session must not execute"
    assert len(tool_executor.calls) == 1


@_PG_SKIP
async def test_admission_drift_refuses_before_resume_gate(
    harness, llm_stub, tool_executor, tooled_agent, monkeypatch
) -> None:
    """Admission drift (revoked principal) refuses BEFORE the gate is consulted."""
    from hecate.models.agent_principal import AgentPrincipalModel, PrincipalLifecycle

    result = await _submit(harness, "admission drift")
    task_ref = result.task_ref
    await _crash_first_dispatch(harness, llm_stub, task_ref, monkeypatch, park=False)

    async with harness["session_factory"]() as db:
        principal = await db.get(AgentPrincipalModel, harness["agent_id"])
        principal.lifecycle = PrincipalLifecycle.REVOKED
        await db.commit()

    # Gate open on purpose: if admission failed to precede the resume gate,
    # execution would proceed and the model-call count would grow.
    llm_stub.gate.set()
    calls_before = len(llm_stub.calls)
    harness["clock"].advance(300.0)
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(harness["store"], _platform_dispatcher(harness), leases=harness["store"].leases)
    await worker.reconcile()

    state = harness["store"].get_task_state(task_ref)
    assert state.lifecycle_state.value == "reconciliation_required", state.extra
    assert "admission no longer valid" in (state.extra or {}).get("reconciliation_reason", "")
    assert len(llm_stub.calls) == calls_before, "revoked principal must not resume"
    assert len(tool_executor.calls) == 1
    runs = await _run_rows(harness, uuid.UUID(task_ref.id))
    assert len(runs) == 1


@_PG_SKIP
async def test_waiting_task_is_never_re_driven(harness, llm_stub, tool_executor, tooled_agent, monkeypatch) -> None:
    """The wake/park path and the continuation gate are mutually exclusive."""
    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher, TaskWaitingSignalError

    result = await _submit(harness, "waiting park")
    task_ref = result.task_ref

    async def _park(self, db, context, **kwargs):
        raise TaskWaitingSignalError(ControlCommandKind.PROVIDE_INPUT, {"tool": TOOL_NAME})

    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", _park)
    from hecate_durable.worker import DurableWorker

    worker = DurableWorker(harness["store"], _platform_dispatcher(harness), leases=harness["store"].leases)
    dispatched = await worker.dispatch_once(task_ref)
    state = harness["store"].get_task_state(task_ref)
    assert dispatched, f"dispatch_once refused; state={state.lifecycle_state} extra={state.extra}"
    assert state.lifecycle_state.value == "waiting_input"

    # Recovery never re-drives a parked wait: reconcile only picks
    # queued/running, and dispatch_once skips non-queued states.
    assert await worker.dispatch_once(task_ref) is False
    assert await worker.reconcile() == 0
    assert harness["store"].get_task_state(task_ref).lifecycle_state.value == "waiting_input"
