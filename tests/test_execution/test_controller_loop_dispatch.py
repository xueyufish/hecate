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

    def __init__(self, port: ScriptedLoopPort) -> None:
        self._port = port
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
        guardrail_platform.assemble_guardrails = _no_guardrails
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
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    @event.listens_for(async_engine.sync_engine, "connect")
    def _wal_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    from hecate_durable.storage import SqlDurableStore

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


async def _dispatch_to_completion(harness, **input_overrides: Any) -> tuple[Any, Any, ScriptedLoopPort]:
    """Submit a controller task and drive it to a terminal durable state."""

    from hecate_durable.worker import DurableWorker

    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    port = input_overrides.pop("port")
    assembly = _ScriptedAssembly(port)
    assembly.patch()
    harness["cleanups"].append(assembly.unpatch)
    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    worker = DurableWorker(harness["store"], dispatcher, leases=harness["store"].leases)
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
        result = await service.submit(
            workspace_id=WS,
            user_id=OWNER,
            goal="run the controller loop",
            agent_id=harness["agent_id"],
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
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
        terminal = {"succeeded", "failed", "cancelled", "reconciliation_required"}
        if record is not None and record.lifecycle_state.value in terminal:
            return record, t_ref, port
        await asyncio.sleep(0.05)
    record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
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
