"""Platform dispatch of the opt-in agent-controller loop (task-control).

Deterministic SQLite coverage for the ``controller_loop`` execution binding
on the platform task path: the dispatcher drives the shared controller over
the durable accepted-decision chain (no Pregel engine session), the final
answer passes the declared output schema before the task may succeed, a
schema violation keeps the delivered content but fails the task, and the
turn cap stops the loop with an explicit category. The model and tool
capabilities are scripted at the same composition seams the real assembly
consumes (``create_runtime_port``, ``load_agent_tools``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from hecate_runtime.ports import StubRuntimePort
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.core.database import Base
from hecate.execution.task_control import TaskControlService, task_ref_of
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.managed_delivery import ManagedDeliveryModel  # noqa: F401
from hecate.models.organization import OrganizationModel
from hecate.models.standalone_enrollment import StandaloneEnrollmentModel  # noqa: F401
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

WS = uuid.UUID("00000000-0000-0000-0000-000000000030")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000032")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000033")

OUTPUT_SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_inventory",
        "description": "query stock",
        "parameters": {
            "type": "object",
            "required": ["sku"],
            "properties": {"sku": {"type": "string"}},
        },
    },
}


class ScriptedLoopPort(StubRuntimePort):
    """Structured model + governed business tool, scripted per turn.

    The first call (no tool results yet) requests the tool with the
    MODEL-chosen arguments; the second call returns the scripted final
    answer. ``final_text`` and ``always_tool`` let tests script the
    rejecting/capping scenarios.
    """

    def __init__(self, *, final_text: str, tool_arguments: dict | None = None, always_tool: bool = False) -> None:
        super().__init__()
        self.final_text = final_text
        self.tool_arguments = tool_arguments or {"sku": "MODEL-CHOSEN"}
        self.always_tool = always_tool
        self.model_calls = 0
        self.tool_calls: list[tuple[str, dict]] = []

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        self.messages = messages
        self.model_config = config
        tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
        if not tool_messages or self.always_tool:
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "query_inventory",
                            "arguments": json.dumps(self.tool_arguments),
                        },
                    }
                ],
            }
            return
        yield {"content": self.final_text, "tool_calls": None}

    async def tool_execute(self, name, args, context=None):
        self.tool_calls.append((name, dict(args)))
        return {"quantity": 3}


class _ScriptedAssembly:
    """Patch the composition seams the controller assembly consumes."""

    def __init__(self, port: ScriptedLoopPort, *, guardrails: Any | None = None) -> None:
        self._port = port
        self._guardrails = guardrails
        self._saved: dict[str, Any] = {}

    def patch(self) -> None:
        import hecate.core.composition.entry_assembly as entry_assembly
        import hecate.core.composition.guardrail_platform as guardrail_platform
        import hecate.core.composition.runtime_port_adapter as port_adapter

        self._saved = {
            "load_agent_tools": entry_assembly.load_agent_tools,
            "build_tool_registry": entry_assembly.build_tool_registry,
            "get_shared_event_store": entry_assembly.get_shared_event_store,
            "assemble_guardrails": guardrail_platform.assemble_guardrails,
            "create_runtime_port": port_adapter.create_runtime_port,
        }

        async def _load_tools(db, tool_names, *, workspace_id=None):
            return [TOOL_DEFINITION]

        entry_assembly.load_agent_tools = _load_tools
        entry_assembly.build_tool_registry = lambda *args, **kwargs: None
        entry_assembly.get_shared_event_store = lambda: None
        guardrail_platform.assemble_guardrails = self._guardrails or _no_guardrails
        port_adapter.create_runtime_port = lambda *args, **kwargs: self._port

    def unpatch(self) -> None:
        import hecate.core.composition.entry_assembly as entry_assembly
        import hecate.core.composition.guardrail_platform as guardrail_platform
        import hecate.core.composition.runtime_port_adapter as port_adapter

        entry_assembly.load_agent_tools = self._saved["load_agent_tools"]
        entry_assembly.build_tool_registry = self._saved["build_tool_registry"]
        entry_assembly.get_shared_event_store = self._saved["get_shared_event_store"]
        guardrail_platform.assemble_guardrails = self._saved["assemble_guardrails"]
        port_adapter.create_runtime_port = self._saved["create_runtime_port"]


async def _no_guardrails(*args: Any, **kwargs: Any) -> None:
    return None


@pytest.fixture
async def harness(tmp_path):
    db_file = tmp_path / "controller_loop.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})

    @event.listens_for(async_engine.sync_engine, "connect")
    def _busy_async(dbapi_conn, _record):
        # The sync durable engine and this engine share the file; both must
        # wait on SQLite's busy timeout instead of failing under contention.
        dbapi_conn.execute("PRAGMA busy_timeout=30000")

    import sqlite3 as _sqlite3

    from hecate_durable.storage import SqlDurableStore

    # Switch the shared file to WAL once, deterministically, before any
    # engine connection exists: a per-connection journal_mode pragma races
    # with concurrent access (a failed switch silently stays in DELETE
    # mode, where one engine's open write transaction blocks the other's
    # reads and writes for the full busy timeout).
    with _sqlite3.connect(db_file) as raw:
        raw.execute("PRAGMA journal_mode=WAL")
    store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")
    store.create_schema()

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.core.composition.durable_platform import DurableSuite

    suite = DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core")

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="controller-loop-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={
                "model": "stub",
                "persona": "Follow the frozen stock policy.",
                "tools": ["query_inventory"],
                "model_config": {"model": "test-model", "temperature": 0.2},
            },
            content_hash="a" * 64,
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
        agent_id = agent.id

    state = {
        "session_factory": session_factory,
        "suite": suite,
        "agent_id": agent_id,
        "engine": async_engine,
        "store": store,
        "cleanups": [],
    }
    yield state

    for cleanup in reversed(state["cleanups"]):
        cleanup()
    await async_engine.dispose()
    store.dispose()


async def _dispatch_to_completion(env, **input_overrides: Any) -> tuple[Any, Any, ScriptedLoopPort]:
    """Submit a controller task and drive it to a terminal durable state."""

    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    port = input_overrides.pop("port")
    assembly = _ScriptedAssembly(port, guardrails=input_overrides.pop("guardrails", None))
    assembly.patch()
    env["cleanups"].append(assembly.unpatch)
    dispatcher = PlatformTaskDispatcher(env["store"], env["session_factory"])
    worker = DurableWorker(env["store"], dispatcher, leases=env["store"].leases)
    async with env["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=env["suite"].store,
            recorder=env["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=env["session_factory"],
            dispatcher=None,
            worker=worker,
        )
        result = await service.submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="run the controller loop",
            agent_id=env["agent_id"],
            input={
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": input_overrides.pop("max_turns", 4)},
                **input_overrides,
            },
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = task_ref_of(task_uuid)
    deadline = asyncio.get_running_loop().time() + 20.0
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(env["store"].get_task_state, t_ref)
        terminal = {"succeeded", "failed", "cancelled", "reconciliation_required"}
        if record is not None and record.lifecycle_state.value in terminal:
            return record, t_ref, port
        await asyncio.sleep(0.05)
    record = await asyncio.to_thread(env["store"].get_task_state, t_ref)
    raise AssertionError(
        f"task never reached a terminal state; last={record.lifecycle_state.value if record else None}; "
        f"extra={record.extra if record else None}"
    )


async def test_controller_loop_finishes_with_schema_validated_delivery(harness) -> None:
    """Two turns over the durable chain; schema-checked final answer succeeds."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, _t_ref, port = await _dispatch_to_completion(harness, port=port, output_schema=OUTPUT_SCHEMA)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == json.dumps({"answer": "three items in stock"})
    # The loop advanced on the tool's real result: two model calls, one
    # governed dispatch with the MODEL-chosen arguments.
    assert port.model_calls == 2
    assert port.tool_calls == [("query_inventory", {"sku": "MODEL-CHOSEN"})]
    assert port.messages[0] == {"role": "system", "content": "Follow the frozen stock policy."}
    assert port.model_config["temperature"] == 0.2
    # The schema pass is recorded as the task's acceptance evaluation.
    acceptance = result["acceptance"]
    assert acceptance["version"] == 1 and acceptance["status"] == "passed"
    assert acceptance["checks"] == [{"kind": "output_schema", "status": "passed"}]


async def test_schema_violation_keeps_content_but_fails_task(harness) -> None:
    """A delivered answer that violates the schema never submits success."""
    port = ScriptedLoopPort(final_text="not json at all")
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, output_schema=OUTPUT_SCHEMA)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == "not json at all"  # the delivered content stays visible
    assert result["error_category"] == "acceptance_failed"
    acceptance = result["acceptance"]
    assert acceptance["version"] == 1
    assert acceptance["status"] == "acceptance_failed"
    assert acceptance["checks"][0]["kind"] == "output_schema"
    assert acceptance["checks"][0]["status"] == "failed"


async def test_turn_cap_stops_with_explicit_category(harness) -> None:
    """The durable turn budget stops an always-tooling model honestly."""
    port = ScriptedLoopPort(final_text="never reached", always_tool=True)
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, max_turns=2)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result["error_category"] == "controller_turn_limit"
    assert port.model_calls == 2
    assert len(port.tool_calls) == 2


async def test_controller_binding_never_mints_attempt_runs(harness) -> None:
    """The loop's continuation is the chain: one platform run for the task."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "ok"}))
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, output_schema=OUTPUT_SCHEMA)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    async with harness["session_factory"]() as db:
        from sqlalchemy import select

        from hecate.models.run import RunModel

        runs = (await db.execute(select(RunModel).where(RunModel.workspace_id == WS))).scalars().all()
    assert len(runs) == 1, [run.id for run in runs]


async def test_unknown_business_outcome_remains_pending_reconciliation(harness) -> None:
    """A lost business receipt must not turn into a terminal task failure."""

    class LostReceiptPort(ScriptedLoopPort):
        """The tool may have completed before its response was lost."""

        async def tool_execute(self, name, args, context=None):
            raise TimeoutError("receipt lost after dispatch")

    port = LostReceiptPort(final_text="never reached")
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert record.extra["reconciliation_reason"] == "action outcome is not established"
    assert port.model_calls == 1


async def test_unreadable_input_history_stops_in_reconciliation_before_model(harness, monkeypatch) -> None:
    """History failure is a recovery stop, not a retryable model execution."""

    def unavailable(*args):
        raise ValueError("input history is corrupt")

    monkeypatch.setattr(harness["store"], "list_input_revisions", unavailable)
    port = ScriptedLoopPort(final_text="must not be asked")
    record, _task_ref, _port = await _dispatch_to_completion(harness, port=port)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert record.extra["result"]["error_category"] == "input_history_unavailable"
    assert port.model_calls == 0 and port.tool_calls == []


async def test_input_history_read_does_not_block_the_dispatch_event_loop(harness, monkeypatch) -> None:
    """Storage waits must let heartbeats and other database sessions progress."""
    import threading

    loop = asyncio.get_running_loop()
    read = harness["store"].list_input_revisions

    def read_after_loop_progress(*args):
        ready = threading.Event()
        loop.call_soon_threadsafe(ready.set)
        if not ready.wait(timeout=2):
            raise ValueError("history read blocked the event loop")
        return read(*args)

    monkeypatch.setattr(harness["store"], "list_input_revisions", read_after_loop_progress)
    port = ScriptedLoopPort(final_text="done")
    record, _task_ref, _port = await _dispatch_to_completion(harness, port=port)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert port.model_calls == 2


@pytest.mark.parametrize("has_new_revision", [False, True])
async def test_legacy_snapshot_is_not_hidden_by_later_revisions(harness, monkeypatch, has_new_revision) -> None:
    """A later revision cannot reconstruct the missing legacy causal boundary."""
    if has_new_revision:
        monkeypatch.setattr(
            harness["store"],
            "list_input_revisions",
            lambda *args: [{"revision_id": "later-input", "content": {"tier": "gold"}, "effective_turn": 1}],
        )
    port = ScriptedLoopPort(final_text="must not be asked")
    task_ref, _task_uuid = await _submit_without_dispatch(harness, port)
    persisted = await asyncio.to_thread(harness["store"].get_task_input, task_ref)
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        task_ref,
        TaskLifecycleState.QUEUED,
        input_payload={**persisted, "provided": {"region": "EU"}},
    )
    record = await _dispatch_to_terminal(harness, task_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert record.extra["result"]["error_category"] == "input_history_unavailable"
    assert port.model_calls == 0 and port.tool_calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_turns": 0},
        {"max_turns": True},
        {"output_schema": "object"},
        {"output_schema": {"$ref": "https://example.invalid/schema"}},
    ],
)
async def test_invalid_controller_contract_is_rejected_before_submission(harness, overrides) -> None:
    """Bad limits or schemas cannot silently become a successful default task."""
    from hecate.execution.task_control import TaskControlValidationError

    with pytest.raises(TaskControlValidationError):
        await _dispatch_to_completion(harness, port=ScriptedLoopPort(final_text="never"), **overrides)
    assert harness["store"].list_tasks() == []


async def test_versioned_acceptance_gates_success_and_records_evaluation(harness) -> None:
    """Declared checks gate the task; the evaluation is recorded with its version."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, _t_ref, _port = await _dispatch_to_completion(
        harness,
        port=port,
        acceptance={
            "version": 3,
            "required_checks": [
                {"kind": "output_schema", "schema": OUTPUT_SCHEMA},
                {"kind": "content_contains", "value": "three items"},
            ],
        },
    )
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    result = (record.extra or {}).get("result") or {}
    acceptance = result["acceptance"]
    assert acceptance["version"] == 3 and acceptance["status"] == "passed"
    assert [check["status"] for check in acceptance["checks"]] == ["passed", "passed"]
    assert len(acceptance["content_sha256"]) == 64


async def test_failed_required_check_keeps_content_and_blocks_success(harness) -> None:
    """A failing content check fails the task with the delivery preserved."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "nothing relevant"}))
    record, _t_ref, _port = await _dispatch_to_completion(
        harness,
        port=port,
        acceptance={
            "version": 1,
            "required_checks": [
                {"kind": "output_schema", "schema": OUTPUT_SCHEMA},
                {"kind": "content_contains", "value": "three items"},
            ],
        },
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == json.dumps({"answer": "nothing relevant"})
    assert result["error_category"] == "acceptance_failed"
    acceptance = result["acceptance"]
    assert acceptance["status"] == "acceptance_failed"
    assert [check["status"] for check in acceptance["checks"]] == ["passed", "failed"]


@pytest.mark.parametrize(
    "acceptance",
    [
        {"version": 0},
        {"version": "1"},
        {"required_checks": "checks"},
        {"required_checks": [{"kind": "unknown_kind"}]},
        {"required_checks": [{"kind": "content_contains", "value": ""}]},
        {"required_checks": [{"kind": "output_schema"}]},
    ],
)
async def test_malformed_acceptance_declaration_is_rejected(harness, acceptance) -> None:
    """Authoring errors fail at submission, not as runtime verdicts."""
    from hecate.execution.task_control import TaskControlValidationError

    with pytest.raises(TaskControlValidationError):
        await _dispatch_to_completion(harness, port=ScriptedLoopPort(final_text="never"), acceptance=acceptance)
    assert harness["store"].list_tasks() == []


class _WakeRegionPort(ScriptedLoopPort):
    """Turn 1 requests the tool; the final answer echoes every accepted input.

    Records the injected input-revision user messages the FIRST model ask
    saw, so tests can assert the wake's values reached the model at the
    right position in the rebuilt conversation.
    """

    def __init__(self) -> None:
        super().__init__(final_text="unused")
        self.first_ask_provided: list[dict] = []
        self.final_sent: str | None = None

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        self.messages = messages
        provided: list[dict] = []
        for message in messages:
            if message.get("role") == "user" and "provided_input" in str(message.get("content")):
                envelope = json.loads(message["content"])
                provided.append(envelope["provided_input"])
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        if not tool_messages:
            self.first_ask_provided = provided
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "query_inventory",
                            "arguments": json.dumps({"sku": "S-9"}),
                        },
                    }
                ],
            }
            return
        final = "inputs:" + json.dumps(provided, sort_keys=True, ensure_ascii=False)
        self.final_sent = final
        yield {"content": final, "tool_calls": None}


async def _submit_without_dispatch(harness, port: _WakeRegionPort, **input_overrides: Any) -> tuple[Any, uuid.UUID]:
    """Real submission (all platform + durable rows) with dispatch suppressed.

    ``submit`` normally spawns a background dispatch — and with
    ``dispatcher=None`` the service falls back to a REAL dispatcher, so that
    spawn would race the test's own explicit worker drives on the shared
    SQLite file (claiming the queued state before the test's fixture
    writes). Absorb the spawn here; every test in this file drives dispatch
    explicitly.
    """
    import hecate.execution.task_control as task_control_module

    assembly = _ScriptedAssembly(port)
    assembly.patch()
    harness["cleanups"].append(assembly.unpatch)
    spawn_background = task_control_module._spawn_background

    def _absorb_spawn(coro) -> None:  # noqa: ANN001 — the coroutine type varies by caller
        coro.close()

    task_control_module._spawn_background = _absorb_spawn
    try:
        return await _submit_rows(harness, input_overrides)
    finally:
        task_control_module._spawn_background = spawn_background


async def _submit_rows(harness, input_overrides: Any) -> tuple[Any, uuid.UUID]:
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["suite"].store,
            recorder=harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=None,
        )
        result = await service.submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="run the controller loop",
            agent_id=harness["agent_id"],
            input={
                "messages": [{"role": "user", "content": "check the stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 4},
                **input_overrides,
            },
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    return task_ref_of(task_uuid), task_uuid


async def _park_for_input(harness, t_ref: Any) -> str:
    """Write a bare input wait record (state + token) for wake-side tests.

    These tests exercise the wake/application side in isolation; the
    production park path (business input_required contract -> dispatcher
    -> _park) is covered end to end by
    ``test_production_input_park_wake_and_consumption_end_to_end``.
    """
    from hecate.contracts.execution.durable import TaskLifecycleState as PlatformTaskState

    await asyncio.to_thread(harness["store"].apply_task_state, t_ref, PlatformTaskState.RUNNING)
    token = uuid.uuid4().hex
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        t_ref,
        PlatformTaskState.WAITING_INPUT,
        extra_update={
            "wait": {
                "wake_kind": "provide_input",
                "contract_ref": {},
                "wait_token": token,
                "wait_expires_at": None,
                "consumed": False,
            }
        },
    )
    return token


async def _issue_input_wake(
    harness, task_uuid: uuid.UUID, wait_token: str, payload: dict, *, command_id: str | None = None, worker: Any
) -> Any:
    """One real ``issue_command`` (PROVIDE_INPUT) through the service.

    The worker is bound like the production service so the wake leaves the
    re-dispatch to the caller instead of spawning a background dispatch.
    """
    from hecate.contracts.execution.durable import ControlCommandKind

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["suite"].store,
            recorder=harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=worker,
        )
        return await service.issue_command(
            workspace_id=WS,
            task_id=task_uuid,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer="tester",
            payload=payload,
            payload_schema_ref="urn:hecate:task-user-input/0" if payload else None,
            command_id=command_id,
            detail_ns={"wait_token": wait_token},
        )


def _worker_for(harness) -> Any:
    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    return DurableWorker(harness["store"], dispatcher, leases=harness["store"].leases)


_TERMINAL_STATES = {"succeeded", "failed", "cancelled", "reconciliation_required"}


async def _dispatch_to_state(harness, t_ref: Any, worker: Any, *targets: str, timeout: float = 90.0) -> Any:
    """Drive worker dispatches until the task reaches one of ``targets``.

    The shared SQLite file serves two engines; a dispatch attempt can hit
    the file's busy timeout under contention — the worker then reports a
    failed attempt and requeues. The durable worker is built for repeated
    attempts (its reconcile cycle re-dispatches queued tasks and recovers
    a running task whose dispatch died mid-flight), so the helper drives
    that same path instead of asserting on one attempt's return value.
    """
    from sqlalchemy.exc import OperationalError

    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if record.lifecycle_state.value in targets:
            return record
        if record.lifecycle_state.value in {"queued", "running"}:
            try:
                await worker.reconcile()
            except OperationalError:
                await asyncio.sleep(0.2)
        await asyncio.sleep(0.05)
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    raise AssertionError(
        f"task never reached {sorted(targets)}; state={record.lifecycle_state.value}; extra={record.extra}"
    )


async def _dispatch_to_terminal(harness, t_ref: Any, *, worker: Any) -> Any:
    return await _dispatch_to_state(harness, t_ref, worker, *_TERMINAL_STATES)


async def test_input_wake_records_revision_and_model_consumes_it(harness) -> None:
    """The wake writes an immutable revision (not a snapshot) the loop reads."""
    port = _WakeRegionPort()
    t_ref, task_uuid = await _submit_without_dispatch(harness, port)
    token = await _park_for_input(harness, t_ref)

    # A wrong token is rejected durably: no revision, no state move.
    worker = _worker_for(harness)
    rejected = await _issue_input_wake(harness, task_uuid, "wrong-token", {"region": "EU"}, worker=worker)
    assert rejected.record.state.value == "rejected"
    assert await asyncio.to_thread(harness["store"].list_input_revisions, t_ref) == []

    issue = await _issue_input_wake(
        harness, task_uuid, token, {"region": "EU"}, command_id="wake-platform-1", worker=worker
    )
    assert issue.record.state.value == "applied"
    assert issue.detail.startswith("wake accepted")
    persisted = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    assert "provided" not in persisted  # never a latest-snapshot overwrite
    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, t_ref)
    assert len(revisions) == 1
    assert revisions[0]["content"] == {"region": "EU"}
    assert revisions[0]["source_ref"] == "wake-platform-1"
    assert revisions[0]["effective_turn"] == 1  # zero committed decisions yet
    receipt = await asyncio.to_thread(harness["suite"].store.get, "wake-platform-1")
    assert receipt.extra.get("input_revision") == revisions[0]["revision_id"]

    # Idempotent replay of the settled command.
    replay = await _issue_input_wake(
        harness, task_uuid, token, {"region": "EU"}, command_id="wake-platform-1", worker=worker
    )
    assert replay.detail == "idempotent replay of a settled command"
    assert len(await asyncio.to_thread(harness["store"].list_input_revisions, t_ref)) == 1

    record = await _dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state.value == "succeeded", record.extra
    # The first model ask saw the accepted input at the conversation tail.
    assert port.first_ask_provided == [{"region": "EU"}]
    result = (record.extra or {}).get("result") or {}
    assert result["content"].startswith("inputs:")


async def test_two_input_wakes_reach_the_model_in_causal_order(harness) -> None:
    """Two waits and two wakes: both revisions arrive in order, one revision each."""
    port = _WakeRegionPort()
    t_ref, task_uuid = await _submit_without_dispatch(harness, port)
    token1 = await _park_for_input(harness, t_ref)
    worker = _worker_for(harness)
    first = await _issue_input_wake(harness, task_uuid, token1, {"region": "EU"}, worker=worker)
    assert first.record.state.value == "applied"

    token2 = await _park_for_input(harness, t_ref)
    second = await _issue_input_wake(harness, task_uuid, token2, {"tier": "gold"}, worker=worker)
    assert second.record.state.value == "applied"

    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, t_ref)
    assert [row["content"] for row in revisions] == [{"region": "EU"}, {"tier": "gold"}]
    assert [row["effective_turn"] for row in revisions] == [1, 1]
    assert [row["source_ref"] for row in revisions] == [first.record.command_id, second.record.command_id]

    record = await _dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state.value == "succeeded", record.extra
    assert port.first_ask_provided == [{"region": "EU"}, {"tier": "gold"}]
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == "inputs:" + json.dumps(
        [{"region": "EU"}, {"tier": "gold"}], sort_keys=True, ensure_ascii=False
    )


async def test_late_input_wakes_resume_accepted_turns_in_fresh_platform_assemblies(harness, monkeypatch) -> None:
    """Host-injected waits after accepted turns preserve causal inputs on replay."""
    from dataclasses import replace

    from hecate_runtime.execution_service import RuntimeEventDecision, runtime_execution_service

    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_dispatcher import TaskWaitingSignalError

    class InputDrivenPort(ScriptedLoopPort):
        """Select business arguments from each causally visible revision."""

        async def llm_invoke_structured(self, messages, config):
            """Request actions until both inputs have produced real results."""
            self.model_calls += 1
            tools = [message for message in messages if message.get("role") == "tool"]
            provided = [
                json.loads(message["content"])["provided_input"]
                for message in messages
                if message.get("role") == "user" and "provided_input" in str(message.get("content"))
            ]
            if len(tools) == 3:
                self.messages = messages
                yield {"content": json.dumps(provided), "tool_calls": None}
                return
            arguments = {"sku": "S-9"}
            for content in provided:
                arguments.update(content)
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{len(tools) + 1}",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": json.dumps(arguments)},
                    }
                ],
            }

    execute = runtime_execution_service.execute
    phase = 1

    async def execute_until_input_boundary(request):
        parked = False

        def observe(event):
            nonlocal parked
            if phase < 3 and event.get("type") == "controller/turn_completed" and event["turn"] == phase:
                parked = True
                return RuntimeEventDecision.STOP_UNKNOWN
            return request.observer(event) if request.observer else RuntimeEventDecision.CONTINUE

        result = await execute(replace(request, observer=observe))
        if parked:
            # Exercise the host's real park path after closing the runtime
            # stream. Input-request detection itself is outside this slice.
            raise TaskWaitingSignalError(ControlCommandKind.PROVIDE_INPUT, {})
        return result

    monkeypatch.setattr(runtime_execution_service, "execute", execute_until_input_boundary)
    port = InputDrivenPort(final_text="unused")
    task_ref, task_uuid = await _submit_without_dispatch(harness, port)
    original_run = harness["store"].run_for_task(task_ref)
    for phase, payload in enumerate(({"region": "EU"}, {"tier": "gold"}), start=1):
        worker = _worker_for(harness)
        parked = await _dispatch_to_state(harness, task_ref, worker, "waiting_input")
        assert parked.lifecycle_state is TaskLifecycleState.WAITING_INPUT
        receipt = await _issue_input_wake(
            harness, task_uuid, parked.extra["wait"]["wait_token"], payload, worker=worker
        )
        assert receipt.record.state.value == "applied"
        assert len(harness["store"].list_input_revisions(task_ref)) == phase
    phase = 3
    record = await _dispatch_to_terminal(harness, task_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    revisions = harness["store"].list_input_revisions(task_ref, original_run)
    assert [row["effective_turn"] for row in revisions] == [2, 3]
    assert harness["store"].run_for_task(task_ref) == original_run
    assert port.model_calls == 4
    assert port.tool_calls == [
        ("query_inventory", {"sku": "S-9"}),
        ("query_inventory", {"sku": "S-9", "region": "EU"}),
        ("query_inventory", {"sku": "S-9", "region": "EU", "tier": "gold"}),
    ]
    messages = port.messages
    inputs = [i for i, message in enumerate(messages) if "provided_input" in str(message.get("content"))]
    tools = [i for i, message in enumerate(messages) if message.get("role") == "tool"]
    assert tools[0] < inputs[0] < tools[1] < inputs[1] < tools[2]


async def _committed_first_decision(harness, payload: dict) -> Any:
    """The run's first committed decision, read back from the durable store."""
    from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
    from hecate_runtime.accepted_decision import RunRef

    return await SqlAcceptedDecisionStore(harness["store"]).lookup(
        RunRef("hecate", payload["platform_run_id"]), "agent-turn-000001"
    )


async def test_declared_harness_sections_compose_base_and_pin_digest(harness) -> None:
    """Caller semantic sections precede host-derived sections; every decision pins the digest."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "ok"}))
    declared = {
        "version": 2,
        "sections": [
            {"type": "instructions", "content": "Consult memory before answering."},
            {"type": "memory", "content": {"policy": "EU stock rules"}},
        ],
    }
    record, t_ref, port = await _dispatch_to_completion(
        harness, port=port, output_schema=OUTPUT_SCHEMA, harness=declared
    )
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    # The caller's declaration rides the durable payload verbatim.
    payload = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    assert payload["harness"] == declared
    # Caller sections first, then the derived persona/schema/task sections.
    assert port.messages[0] == {"role": "system", "content": "Consult memory before answering."}
    assert port.messages[1] == {"role": "system", "content": '{"memory":{"policy":"EU stock rules"}}'}
    assert port.messages[2] == {"role": "system", "content": "Follow the frozen stock policy."}
    assert "satisfying this schema" in port.messages[3]["content"]
    # Turn 2's recorded input: the four system sections, then the task's
    # user message, the tool-call turn and its governed result.
    assert port.messages[4] == {"role": "user", "content": "check the stock"}
    assert port.messages[-1]["role"] == "tool"
    decision = await _committed_first_decision(harness, payload)
    assert decision is not None
    assert decision.input_refs["harness_version"] == 2
    assert decision.input_refs["harness_digest"]


async def test_default_submission_pins_the_default_composition(harness) -> None:
    """A controller task without caller sections is stamped the default block."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "ok"}))
    record, t_ref, port = await _dispatch_to_completion(harness, port=port)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    payload = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    assert payload["harness"] == {"version": 1}
    # The composed base keeps the historical manual shape byte-for-byte.
    assert port.messages[0] == {"role": "system", "content": "Follow the frozen stock policy."}
    assert port.messages[1] == {"role": "user", "content": "check the stock"}
    decision = await _committed_first_decision(harness, payload)
    assert decision is not None
    assert decision.input_refs["harness_version"] == 1
    assert decision.input_refs["harness_digest"]


async def test_pre_binding_payload_routes_to_manual_composition(harness) -> None:
    """Tasks submitted before the binding carry no block: manual composition, no pinning."""
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    payload = {
        "workspace_id": str(WS),
        "agent_id": str(harness["agent_id"]),
        "messages": [{"role": "user", "content": "legacy"}],
        "controller_loop": True,
    }
    context = PlatformTaskDispatcher._context_of(task_ref_of(uuid.uuid4()), payload)
    assert context.controller_loop is True
    assert context.harness is None


@pytest.mark.parametrize("block", [[], "corrupt", False, {"sections": [{"type": "persona", "content": "override"}]}])
async def test_corrupt_persisted_harness_cannot_fall_back_to_legacy_composition(harness, block) -> None:
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    payload = {
        "workspace_id": str(WS),
        "agent_id": str(harness["agent_id"]),
        "messages": [{"role": "user", "content": "task"}],
        "controller_loop": True,
        "harness": block,
    }
    with pytest.raises(ValueError, match="must be an object|not authorable"):
        PlatformTaskDispatcher._context_of(task_ref_of(uuid.uuid4()), payload)


async def test_harness_declaration_is_validated_at_submission(harness) -> None:
    """Malformed or non-authorable declarations never reach dispatch or model usage."""
    from hecate.execution.task_control import TaskControlService, TaskControlValidationError

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["suite"].store,
            recorder=harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=None,
        )
        base_input = {"messages": [{"role": "user", "content": "check the stock"}]}
        with pytest.raises(TaskControlValidationError, match="requires controller_loop"):
            await service.submit(
                workspace_id=WS,
                user_id=OWNER,
                goal="g",
                agent_id=harness["agent_id"],
                input={**base_input, "harness": {"version": 1}},
            )
        with pytest.raises(TaskControlValidationError, match="not authorable"):
            await service.submit(
                workspace_id=WS,
                user_id=OWNER,
                goal="g",
                agent_id=harness["agent_id"],
                input={
                    **base_input,
                    "controller_loop": True,
                    "harness": {"sections": [{"type": "persona", "content": "caller override"}]},
                },
            )
        with pytest.raises(TaskControlValidationError, match="unknown harness section type"):
            await service.submit(
                workspace_id=WS,
                user_id=OWNER,
                goal="g",
                agent_id=harness["agent_id"],
                input={
                    **base_input,
                    "controller_loop": True,
                    "harness": {"sections": [{"type": "planner", "content": "be smart"}]},
                },
            )


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("stage", ["pre_llm", "llm_response"])
async def test_guard_rejected_model_call_fails_the_task_with_category(harness, with_tools, stage) -> None:
    """A pre-call guard BLOCK refuses the turn; no model usage, classified stop."""
    from types import SimpleNamespace

    from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostLLMHook, PreLLMHook
    from hecate_runtime.middleware_factory import build_llm_chain

    if not with_tools:
        from sqlalchemy import select

        async with harness["session_factory"]() as db:
            version = (await db.scalars(select(AgentVersionModel))).one()
            version.config_snapshot = {**version.config_snapshot, "tools": []}
            await db.commit()

    class _Blocking(PreLLMHook):
        async def on_pre_llm_call(self, messages, model, tools):
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="prompt injection suspected")

    class _BlockingOutput(PostLLMHook):
        async def on_post_llm_call(self, response, messages):
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="unsafe output")

    async def _blocking_bundle(*args: Any, **kwargs: Any):
        return SimpleNamespace(
            access_policy=None,
            approval_callback=None,
            rules=[],
            middleware_chains=build_llm_chain(
                pre_hook=_Blocking() if stage == "pre_llm" else None,
                post_hook=_BlockingOutput() if stage == "llm_response" else None,
            ),
            denial_tracker=None,
        )

    port = ScriptedLoopPort(final_text=json.dumps({"answer": "never"}))
    record, _t_ref, port = await _dispatch_to_completion(harness, port=port, guardrails=_blocking_bundle)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {}).get("result") or {}
    assert result["error_category"] == "controller_model_call_rejected"
    assert (result["error"] or "").endswith("model_call_rejected")
    assert port.model_calls == (0 if stage == "pre_llm" else 1)
    assert port.tool_calls == []


async def test_context_budget_stop_never_marks_task_succeeded(harness) -> None:
    port = ScriptedLoopPort(final_text="must never be accepted")
    record, t_ref, port = await _dispatch_to_completion(
        harness,
        port=port,
        harness={"sections": [{"type": "instructions", "content": "x" * 40000}]},
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["result"]["error_category"] == "controller_token_capped"
    assert port.model_calls == 0 and port.tool_calls == []
    payload = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    assert await _committed_first_decision(harness, payload) is None


class _ProductionInputPort(ScriptedLoopPort):
    """Business tool that declares the explicit input-required contract.

    First business call (no region) returns the structured input request —
    no side effect performed per the contract; the completed call serves
    real data. The model asks twice fresh after the wake (region call +
    final), replaying turn 1 from storage with zero model calls.
    """

    def __init__(self) -> None:
        super().__init__(final_text="unused")
        self.business_calls: list[dict] = []

    async def tool_execute(self, name, args, context=None):
        self.business_calls.append(dict(args))
        if "region" not in args:
            return {"status": "input_required", "contract": {"fields": ["region"]}}
        return {"quantity": 3, "region": args["region"]}

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        self.messages = messages
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        provided = [
            json.loads(m["content"])["provided_input"]
            for m in messages
            if m.get("role") == "user" and "provided_input" in str(m.get("content"))
        ]
        if not tool_messages:
            first_call = {
                "id": "call-1",
                "type": "function",
                "function": {"name": "query_inventory", "arguments": json.dumps({"sku": "S-9"})},
            }
            yield {"content": None, "tool_calls": [first_call]}
        elif len(tool_messages) == 1 and provided:
            # The input request's real outcome and the accepted revision are
            # both visible: complete the request with the provided region.
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {
                            "name": "query_inventory",
                            "arguments": json.dumps({"sku": "S-9", "region": "FROM-PROVIDED"}),
                        },
                    }
                ],
            }
        else:
            yield {"content": "region:" + json.dumps(provided), "tool_calls": None}


async def test_production_input_park_wake_and_consumption_end_to_end(harness) -> None:
    """The full loop without any hand-written wait record.

    The business tool's structured input request parks the controller task
    through the dispatcher's production path; the real provide_input
    command records the durable revision and acknowledges that exact action;
    the SAME run continues from the accepted-decision chain, the tool
    consumes the provided fields, and the delivery passes. Committed turns
    replay with zero model calls and the business tool is never re-run
    without region.
    """
    port = _ProductionInputPort()
    t_ref, task_uuid = await _submit_without_dispatch(harness, port)
    worker = _worker_for(harness)

    record = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
    assert record.lifecycle_state.value == "waiting_input"
    wait = (record.extra or {}).get("wait") or {}
    assert wait["wake_kind"] == "provide_input"
    assert wait["consumed"] is False
    contract_ref = wait["contract_ref"]
    assert contract_ref["tool"] == "query_inventory"
    assert contract_ref["execution_id"]
    assert contract_ref["contract"] == {"fields": ["region"]}
    assert port.business_calls == [{"sku": "S-9"}]  # no side effect performed

    # A wake missing a contract-required field is rejected; the token stays
    # unconsumed and no revision is recorded.
    missing = await _issue_input_wake(harness, task_uuid, wait["wait_token"], {"tier": "gold"}, worker=worker)
    assert missing.record.state.value == "rejected"
    assert "wait contract" in (missing.detail or "")
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert record.lifecycle_state.value == "waiting_input"
    assert (record.extra or {})["wait"]["consumed"] is False
    assert await asyncio.to_thread(harness["store"].list_input_revisions, t_ref) == []

    issue = await _issue_input_wake(
        harness, task_uuid, wait["wait_token"], {"region": "EU"}, command_id="wake-prod-1", worker=worker
    )
    assert issue.record.state.value == "applied"
    persisted = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    assert persisted["_input_wait_acknowledgements"] == [contract_ref["execution_id"]]

    record = await _dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state.value == "succeeded", record.extra
    # The provided region reached the model, which completed the request
    # WITH it; the parked call never re-ran without region.
    assert port.business_calls == [{"sku": "S-9"}, {"sku": "S-9", "region": "FROM-PROVIDED"}]
    # Three fresh model asks total: turn 1 pre-park, region call, final.
    assert port.model_calls == 3
    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, t_ref)
    assert [row["content"] for row in revisions] == [{"region": "EU"}]
    result = (record.extra or {}).get("result") or {}
    assert result["content"] == "region:" + json.dumps([{"region": "EU"}])


def test_input_acknowledgement_is_bound_to_action_identity() -> None:
    """An old outcome stays acknowledged; a fresh identical call still parks."""
    from hecate.execution.task_dispatcher import _ControllerInputWait

    watch = _ControllerInputWait(["old-action"])
    result = {"status": "input_required", "contract": {"fields": ["region"]}}
    watch.observe_result("old-action", "query_inventory", {"sku": "S-9"}, result)
    assert watch.requested is None
    watch.observe_result("fresh-action", "query_inventory", {"sku": "S-9"}, result)
    assert watch.requested["execution_id"] == "fresh-action"


async def test_workflow_child_wait_rejects_plain_provide_input(harness) -> None:
    """A workflow child-result wait is only wakeable by the verified callback.

    The parent's wait contract names the awaited child (await_task_ref); a
    human input form carrying even the correct token must not advance it —
    the token is rejected without being consumed and the parent stays
    waiting_input for the callback path.
    """
    from hecate.contracts.execution.durable import TaskLifecycleState as PlatformTaskState

    port = _ProductionInputPort()
    t_ref, task_uuid = await _submit_without_dispatch(harness, port)
    await asyncio.to_thread(harness["store"].apply_task_state, t_ref, PlatformTaskState.RUNNING)
    token = uuid.uuid4().hex
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        t_ref,
        PlatformTaskState.WAITING_INPUT,
        extra_update={
            "wait": {
                "wake_kind": "provide_input",
                "contract_ref": {
                    "await_task_ref": {"issuer_domain": "hecate", "id": str(uuid.uuid4()), "kind": "task"}
                },
                "wait_token": token,
                "wait_expires_at": None,
                "consumed": False,
            }
        },
    )
    rejected = await _issue_input_wake(harness, task_uuid, token, {"region": "EU"}, worker=_worker_for(harness))
    assert rejected.record.state.value == "rejected"
    assert "callback" in (rejected.detail or "")
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
    assert record.lifecycle_state.value == "waiting_input"
    assert (record.extra or {})["wait"]["consumed"] is False


async def test_input_wait_recovers_after_outcome_commit_before_park(harness, monkeypatch) -> None:
    """A new worker recovers the wait from the ledger, without another call."""
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    port = _ProductionInputPort()
    t_ref, _task_uuid = await _submit_without_dispatch(harness, port)
    original = PlatformTaskDispatcher._park

    async def crash_before_park(*args, **kwargs):
        raise asyncio.CancelledError("simulated process exit before durable park")

    monkeypatch.setattr(PlatformTaskDispatcher, "_park", crash_before_park)
    with pytest.raises(asyncio.CancelledError):
        await _worker_for(harness).dispatch_once(t_ref)
    assert harness["store"].get_task_state(t_ref).lifecycle_state is TaskLifecycleState.RUNNING
    monkeypatch.setattr(PlatformTaskDispatcher, "_park", original)
    await _worker_for(harness).reconcile()
    record = harness["store"].get_task_state(t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert (record.extra or {})["wait"]["contract_ref"]["execution_id"]
    assert port.model_calls == 1
    assert port.business_calls == [{"sku": "S-9"}]


class _RepeatedInputPort(_ProductionInputPort):
    """Two separate actions need input despite sharing identical arguments."""

    async def llm_invoke_structured(self, messages, config):
        self.model_calls += 1
        inputs = [m for m in messages if m.get("role") == "user" and "provided_input" in str(m.get("content"))]
        if len(inputs) >= 2:
            yield {"content": "done", "tool_calls": None}
        else:
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{len(inputs)}",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": json.dumps({"sku": "S-9"})},
                    }
                ],
            }


async def test_fresh_identical_input_requests_park_across_multiple_wakes(harness) -> None:
    port = _RepeatedInputPort()
    t_ref, task_uuid = await _submit_without_dispatch(harness, port)
    identities = []
    for index in range(2):
        worker = _worker_for(harness)
        record = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
        wait = (record.extra or {})["wait"]
        identities.append(wait["contract_ref"]["execution_id"])
        issue = await _issue_input_wake(harness, task_uuid, wait["wait_token"], {"region": f"R{index}"}, worker=worker)
        assert issue.record.state.value == "applied"
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert len(set(identities)) == 2
    assert port.business_calls == [{"sku": "S-9"}, {"sku": "S-9"}]
    assert port.model_calls == 3


async def test_post_tool_guard_removes_input_request_before_parking(harness) -> None:
    """Raw tool envelopes must never bypass result sanitization into wait data."""
    from types import SimpleNamespace

    from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostToolHook
    from hecate_runtime.middleware_factory import build_tool_chain

    class _Sanitize(PostToolHook):
        async def on_post_tool_call(self, name, result, context):
            return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"result": "safe feedback"})

    async def bundle(*args, **kwargs):
        return SimpleNamespace(
            access_policy=None,
            approval_callback=None,
            rules=[],
            middleware_chains=build_tool_chain(pre_hook=None, post_hook=_Sanitize()),
            denial_tracker=None,
        )

    port = _ProductionInputPort()
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, guardrails=bundle)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert "wait" not in (record.extra or {})


@pytest.mark.parametrize(
    "contract",
    [
        {},
        {"fields": []},
        {"fields": "region"},
        {"fields": ["region", "region"]},
        {"fields": [""]},
        {"fields": [None]},
        {"fields": ["approval_ref"]},
        {"fields": ["region"], "prompt": 3},
    ],
)
def test_unknown_input_contract_never_parks(contract) -> None:
    from hecate.execution.task_dispatcher import _ControllerInputWait

    watch = _ControllerInputWait([])
    watch.observe_result("action", "tool", {}, {"status": "input_required", "contract": contract})
    assert watch.requested is None
