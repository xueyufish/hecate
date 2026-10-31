"""Platform host driver for the Controller two-host baseline.

Drives the real platform controller-loop assembly: durable task dispatch
(``TaskControlService`` + ``DurableWorker`` + ``PlatformTaskDispatcher``),
the production runtime port with its governance facade, the durable action
ledger hook and the guardrail bundle assembled from seeded policy rows. Only
two seams are synthetic: the business tool registry (local synthetic data)
and the LLM gateway (the provider under test). Approval waits park through
the real ``ApprovalWaitSignal`` path and resume through the real
``ApprovalDecisionService`` after a full host reassembly over the same
persisted SQLite state.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event as sqla_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.core.database import Base
from tests.scenarios.baseline import controller_host_spec as spec


def _register_platform_models() -> None:
    """Import every model module the dispatch flow touches so create_all
    sees the full schema (standalone runs import no other test modules)."""

    import hecate.enterprise.governance_ledgers  # noqa: F401
    import hecate.execution.task_control  # noqa: F401
    import hecate.execution.task_dispatcher  # noqa: F401
    import hecate.execution.task_run_registry  # noqa: F401
    import hecate.models.approval  # noqa: F401
    import hecate.models.managed_delivery  # noqa: F401
    import hecate.models.quota  # noqa: F401
    import hecate.models.standalone_enrollment  # noqa: F401
    import hecate.models.tool_policy  # noqa: F401


WS = uuid.UUID("00000000-0000-0000-0000-0000000000b0")
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
OWNER = uuid.UUID("00000000-0000-0000-0000-0000000000b3")
APPROVER = uuid.UUID("00000000-0000-0000-0000-0000000000b4")

TERMINAL_STATES = {"succeeded", "failed", "cancelled", "reconciliation_required"}
# Pre-registered per-case cap on the whole collection attempt (seconds).
CASE_TIME_BUDGET_SECONDS = 120.0


class _SyntheticRegistry:
    """Synthetic business boundary executed behind the real governance facade."""

    def __init__(self, case: str) -> None:
        self.case = case
        self.calls: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.side_effects = 0
        self.write_attempts = 0

    async def describe(self, name: str) -> dict[str, Any]:
        return {"parameters": spec.TOOL_SCHEMAS[name]}

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == spec.READ_TOOL:
            if self.case == "unknown_outcome":
                self.receipts.append(
                    {"tool": name, "arguments": dict(args), "status": "outcome_unknown", "content": None}
                )
                raise TimeoutError("synthetic lost read receipt")
            self.receipts.append(
                {"tool": name, "arguments": dict(args), "status": "succeeded", "content": dict(spec.MATERIAL)}
            )
            return dict(spec.MATERIAL)
        if name == spec.WRITE_TOOL:
            self.write_attempts += 1
            self.side_effects += 1
            self.receipts.append(
                {
                    "tool": name,
                    "arguments": dict(args),
                    "status": "succeeded",
                    "content": {"status": "ok", "update_id": spec.RECEIPT_ID},
                }
            )
            return {"status": "ok", "update_id": spec.RECEIPT_ID}
        raise ValueError("unsupported tool")


class _RecordingGateway:
    """The ``hecate_llm.service.llm_service`` seam: provider under recording.

    Exposes the provider's complete choice as one streamed delta in the
    LiteLLM chunk shape the production port consumes. Every round records
    the exact model input, the chosen tool calls, usage and latency; a
    provider failure keeps the rounds already collected.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.phase = "initial"
        self.rounds: list[dict[str, Any]] = []

    async def chat_stream(self, **kwargs: Any):
        round_record: dict[str, Any] = {
            "index": len(self.rounds),
            "phase": self.phase,
            "input_messages": json.loads(json.dumps(kwargs.get("messages") or [], default=str)),
            "tools_enabled": bool(kwargs.get("tools")),
            "input_tools": json.loads(json.dumps(kwargs.get("tools") or [])),
        }
        started = time.monotonic()
        try:
            reply = await self.provider.complete(messages=kwargs.get("messages") or [], tools=kwargs.get("tools") or [])
        except Exception as exc:  # noqa: BLE001 — the failure is the observation
            self.rounds.append(
                {
                    **round_record,
                    "tool_calls": [],
                    "final": False,
                    "content": None,
                    "usage": None,
                    "error_type": type(exc).__name__,
                    "latency_ms": (time.monotonic() - started) * 1000,
                }
            )
            raise
        calls = reply.get("tool_calls") or []
        usage = reply.get("usage")
        self.rounds.append(
            {
                **round_record,
                "tool_calls": json.loads(json.dumps(calls, default=str)),
                "final": not calls,
                "latency_ms": (time.monotonic() - started) * 1000,
                "content": reply.get("content"),
                "usage": usage,
                "provider_model": reply.get("provider_model"),
            }
        )
        yield {
            "content": reply.get("content") or "",
            "tool_calls": [
                SimpleNamespace(
                    index=index,
                    id=call.get("id"),
                    type="function",
                    function=SimpleNamespace(
                        name=call.get("function", {}).get("name"),
                        arguments=_arguments_json(call.get("function", {}).get("arguments")),
                    ),
                )
                for index, call in enumerate(calls)
            ],
        }
        if usage:
            yield {"content": "", "usage": usage}


def _arguments_json(arguments: Any) -> str:
    """LiteLLM deltas carry arguments as a JSON string; normalize dicts."""

    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments or {}, ensure_ascii=False)


class _SeamPatch:
    """Patch the composition seams; keep the real runtime port and guardrails."""

    def __init__(self, registry: _SyntheticRegistry, gateway: _RecordingGateway, event_store: Any) -> None:
        self._registry = registry
        self._gateway = gateway
        self._event_store = event_store
        self._saved: dict[str, Any] = {}

    def patch(self) -> None:
        import hecate_llm.service as llm_service_module

        import hecate.core.composition.entry_assembly as entry_assembly

        self._saved = {
            "load_agent_tools": entry_assembly.load_agent_tools,
            "build_tool_registry": entry_assembly.build_tool_registry,
            "get_shared_event_store": entry_assembly.get_shared_event_store,
            "llm_service": llm_service_module.llm_service,
        }
        definitions = spec.tool_definitions()

        async def _load_tools(db, tool_names, *, workspace_id=None):  # noqa: ANN001 — seam signature
            return definitions

        entry_assembly.load_agent_tools = _load_tools
        entry_assembly.build_tool_registry = lambda *args, **kwargs: self._registry
        entry_assembly.get_shared_event_store = lambda: self._event_store
        llm_service_module.llm_service = self._gateway

    def unpatch(self) -> None:
        import hecate_llm.service as llm_service_module

        import hecate.core.composition.entry_assembly as entry_assembly

        entry_assembly.load_agent_tools = self._saved["load_agent_tools"]
        entry_assembly.build_tool_registry = self._saved["build_tool_registry"]
        entry_assembly.get_shared_event_store = self._saved["get_shared_event_store"]
        llm_service_module.llm_service = self._saved["llm_service"]


class PlatformEnvironment:
    """One SQLite-backed platform assembly seeded for the baseline scenarios."""

    def __init__(self, root: Path, label: str) -> None:
        self.db_file = root / f"platform-{label}.db"
        raw = sqlite3.connect(self.db_file)
        # The sync durable engine and the async ORM engine share this file;
        # WAL must be switched once before any engine connection exists.
        raw.execute("PRAGMA journal_mode=WAL")
        raw.close()

        from hecate_durable.storage import SqlDurableStore

        from hecate.core.composition.durable_platform import DurableSuite

        self.store = SqlDurableStore(f"sqlite:///{self.db_file}", source="platform")
        self.store.create_schema()
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_file}", connect_args={"timeout": 30})

        @sqla_event.listens_for(self._engine.sync_engine, "connect")
        def _busy(dbapi_conn, _record):  # noqa: ANN001 — SQLAlchemy listener signature
            dbapi_conn.execute("PRAGMA busy_timeout=30000")

        self.session_factory = async_sessionmaker(self._engine, class_=AsyncSession, expire_on_commit=False)
        self.suite = DurableSuite(
            store=self.store, recorder=self.store, ledger=self.store, backend="postgres", ledger_source="core"
        )
        self._seeded = False

    async def _seed(self, *, with_approval_rule: bool) -> uuid.UUID:
        """Seed org/workspace/users/agent plus the optional ASK policy row."""

        from hecate.models.agent import AgentModel
        from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
        from hecate.models.agent_principal import AgentPrincipalModel
        from hecate.models.agent_version import AgentVersionModel
        from hecate.models.organization import OrganizationModel
        from hecate.models.tool_policy import ToolPolicyRuleModel
        from hecate.models.user import UserModel
        from hecate.models.workspace import WorkspaceModel

        _register_platform_models()
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with self.session_factory() as db:
            db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
            db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
            db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
            db.add(UserModel(id=APPROVER, email="approver@example.com", hashed_password=uuid.uuid4().hex))
            agent = AgentModel(workspace_id=WS, name="controller-host-baseline-agent")
            db.add(agent)
            await db.flush()
            version = AgentVersionModel(
                agent_id=agent.id,
                version=1,
                config_snapshot={
                    "model": "baseline",
                    "persona": spec.SYSTEM_PROMPT,
                    "tools": [spec.READ_TOOL, spec.WRITE_TOOL],
                    "model_config": {"model": "baseline", "temperature": 0},
                },
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
            if with_approval_rule:
                db.add(
                    ToolPolicyRuleModel(
                        workspace_id=WS,
                        agent_id=None,
                        tool_pattern=spec.WRITE_TOOL,
                        action="ask",
                        priority=10,
                        arg_conditions={},
                    )
                )
            # The production governance refuses metered model calls for a
            # workspace without any quota row (fail closed); seed the real
            # quota the collection runs under so reserve/settle stay live.
            from hecate.models.quota import QuotaModel

            db.add(
                QuotaModel(
                    name="controller-host-baseline-quota",
                    resource_type="cost",
                    scope="workspace",
                    scope_id=WS,
                    workspace_id=WS,
                    limit_value=1000.0,
                    window_type="monthly",
                )
            )
            await db.commit()
            self._seeded = True
            return agent.id

    def make_worker(self) -> Any:
        from hecate_durable.worker import DurableWorker

        from hecate.execution.task_dispatcher import PlatformTaskDispatcher

        dispatcher = PlatformTaskDispatcher(self.store, self.session_factory)
        return DurableWorker(self.store, dispatcher, leases=self.store.leases)

    async def run_rows(self, task_id: uuid.UUID) -> list[Any]:
        from hecate.execution.task_run_registry import TaskRunRegistry

        async with self.session_factory() as db:
            return list(await TaskRunRegistry(db).list_runs_for_task(task_id, WS))

    async def close(self) -> None:
        await self._engine.dispose()
        self.store.dispose()


def _submit_payload(case: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": spec.USER_PROMPT}],
        "controller_loop": True,
        "controller": {"max_turns": 8},
        "output_schema": spec.OUTPUT_SCHEMA,
    }
    if case in ("approval_resume", "cancelled"):
        payload["approval_wait"] = True
    if case == "budget_exhausted":
        # The declared harness section exceeds the pinned context budget, so
        # the platform's context chain stops the turn before any model call.
        payload["harness"] = {"sections": [{"type": "instructions", "content": "x" * 40000}]}
    return payload


async def _wait_for_state(
    environment: PlatformEnvironment,
    task_id: uuid.UUID,
    targets: set[str],
    *,
    worker: Any | None = None,
    timeout: float = CASE_TIME_BUDGET_SECONDS,
) -> Any:
    from hecate_durable.contracts.references import BackendRef, RefKind

    t_ref = BackendRef(RefKind.TASK, "hecate", str(task_id))
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if worker is not None:
            await worker.reconcile()
        record = await asyncio.to_thread(environment.store.get_task_state, t_ref)
        if record is not None and record.lifecycle_state.value in targets:
            return record
        await asyncio.sleep(0.05)
    record = await asyncio.to_thread(environment.store.get_task_state, t_ref)
    raise TimeoutError(
        f"task never reached {sorted(targets)}; last={record.lifecycle_state.value if record else None}; "
        f"extra={json.dumps(record.extra, default=str)[:2000] if record else None}"
    )


async def _observation_base(
    environment: PlatformEnvironment,
    task_id: uuid.UUID,
    gateway: _RecordingGateway,
    registry: _SyntheticRegistry,
    started: float,
) -> dict[str, Any]:
    from hecate.execution.task_dispatcher import run_row_ref

    runs = await environment.run_rows(task_id)
    actions: list[dict[str, Any]] = []
    if runs:
        actions = await asyncio.to_thread(environment.store.list_run_actions, run_row_ref(runs[-1]))
    return {
        "task_id": str(task_id),
        "run_id": str(runs[-1].id) if runs else None,
        "run_count": len(runs),
        "rounds": list(gateway.rounds),
        "tool_calls": list(registry.calls),
        "tool_receipts": list(registry.receipts),
        "ledger_actions": [
            {
                "action_key": action.get("action_key") or (action.get("intent") or {}).get("action_key"),
                "arguments_digest": (action.get("intent") or {}).get("arguments_digest"),
                "side_effect_class": (action.get("intent") or {}).get("side_effect_class"),
                "pending_reconciliation": bool(action.get("pending_reconciliation")),
                "active_claim": bool(action.get("active_claim")),
            }
            for action in actions
        ],
        "latency_ms_total": (time.monotonic() - started) * 1000,
    }


def _fallback_observation(
    gateway: _RecordingGateway, registry: _SyntheticRegistry, started: float, error: Exception
) -> dict[str, Any]:
    return {
        "task_id": None,
        "run_id": None,
        "run_count": None,
        "task_status": None,
        "final_content": None,
        "error_category": None,
        "error": None,
        "reconciliation_reason": None,
        "model_calls": len(gateway.rounds),
        "rounds": list(gateway.rounds),
        "tool_calls": list(registry.calls),
        "tool_receipts": list(registry.receipts),
        "ledger_actions": [],
        "write_attempts": registry.write_attempts,
        "side_effect_count": registry.side_effects,
        "approval": None,
        "rebuild": {"performed": False},
        "post_resume_model_input_has_receipt": None,
        "latency_ms_total": (time.monotonic() - started) * 1000,
        "collection_error": f"{type(error).__name__}: {error}",
    }


async def run_platform_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    """Run one baseline case through the real platform controller assembly."""

    from hecate_durable.contracts.durable import ControlCommandKind
    from hecate_runtime.eventstore import InMemoryEventStore

    environment_label = f"{case}-{uuid.uuid4().hex[:8]}"
    environment = PlatformEnvironment(root, environment_label)
    event_store = InMemoryEventStore()
    registry = _SyntheticRegistry(case)
    gateway = _RecordingGateway(provider)
    patch = _SeamPatch(registry, gateway, event_store)
    started = time.monotonic()
    observation: dict[str, Any] = {}
    from hecate.execution.task_control import _background_tasks

    previous_tasks = set(_background_tasks)
    try:
        async with asyncio.timeout(CASE_TIME_BUDGET_SECONDS):
            patch.patch()
            worker = environment.make_worker()
            agent_id = await environment._seed(with_approval_rule=case in ("approval_resume", "cancelled"))
            payload = _submit_payload(case)
            async with environment.session_factory() as db:
                from hecate.execution.task_control import TaskControlService

                service = TaskControlService(
                    db,
                    store=environment.suite.store,
                    recorder=environment.suite.recorder,
                    backend="postgres",
                    ledger_source="core",
                    session_factory=environment.session_factory,
                    dispatcher=None,
                    worker=worker,
                )
                result = await service.submit(
                    workspace_id=WS,
                    user_id=OWNER,
                    goal="controller host baseline case",
                    agent_id=agent_id,
                    input=payload,
                )
            task_id = uuid.UUID(result.task_ref.id)

            if case in ("approval_resume", "cancelled"):
                record = await _wait_for_state(environment, task_id, {"waiting_approval"})
                wait = (record.extra or {}).get("wait") or {}
                contract = wait.get("contract_ref") or {}
                approval: dict[str, Any] = {
                    "parked": True,
                    "wait_token": wait.get("wait_token"),
                    "approval_ref": contract.get("approval_ref"),
                    "engine_session_id": contract.get("engine_session_id"),
                    "write_attempts_before_park": registry.write_attempts,
                    "decisions": [],
                }
                if case == "cancelled":
                    async with environment.session_factory() as db:
                        from hecate.execution.task_control import TaskControlService

                        control = TaskControlService(
                            db,
                            store=environment.suite.store,
                            recorder=environment.suite.recorder,
                            backend="postgres",
                            ledger_source="core",
                            session_factory=environment.session_factory,
                            dispatcher=None,
                            worker=worker,
                        )
                        issue = await control.issue_command(
                            workspace_id=WS,
                            task_id=task_id,
                            kind=ControlCommandKind.CANCEL,
                            issuer=str(OWNER),
                        )
                    approval["cancel_receipt"] = str(issue.detail)
                    record = await _wait_for_state(environment, task_id, TERMINAL_STATES)
                else:
                    # Reopen SQL state and drop the old in-memory event store so
                    # recovery cannot silently depend on the first assembly.
                    if await worker.drain(timeout=5):
                        raise RuntimeError("platform dispatch did not drain before host rebuild")
                    await environment.close()
                    environment = PlatformEnvironment(root, environment_label)
                    patch._event_store = InMemoryEventStore()
                    gateway.phase = "post_rebuild"
                    fresh_worker = environment.make_worker()
                    from hecate.execution.approval_decision import ApprovalDecisionService

                    async with environment.session_factory() as db:
                        decisions = ApprovalDecisionService(
                            db,
                            store=environment.suite.store,
                            recorder=environment.suite.recorder,
                            backend="postgres",
                            ledger_source="core",
                            session_factory=environment.session_factory,
                            worker=fresh_worker,
                        )
                        decision = await decisions.submit_decision(
                            WS,
                            task_id,
                            operation_id=str(uuid.uuid4()),
                            approval_ref=contract["approval_ref"],
                            decision="approve",
                            reason="baseline approval",
                            wait_token=wait["wait_token"],
                            expected_revision=record.revision,
                            decided_by=str(APPROVER),
                        )
                    approval["decisions"] = [
                        {"approved": True, "result": json.loads(json.dumps(decision, default=str))}
                    ]
                    record = await _wait_for_state(environment, task_id, TERMINAL_STATES, worker=fresh_worker)
                observation.update(await _observation_base(environment, task_id, gateway, registry, started))
                observation["approval"] = approval
                if case == "approval_resume":
                    observation["rebuild"] = {
                        "performed": True,
                        "scope": "in-process host reassembly over the persisted sqlite state",
                        "same_run": observation["run_count"] == 1 and observation["run_id"] == contract.get("run_id"),
                        "run_id": observation["run_id"],
                    }
                    post_resume_rounds = [r for r in observation["rounds"] if r.get("phase") == "post_rebuild"]
                    observation["post_resume_model_input_has_receipt"] = any(
                        spec.RECEIPT_ID in json.dumps(round.get("input_messages"), default=str)
                        for round in post_resume_rounds
                    )
                    observation["post_resume_model_input_has_read_receipt"] = any(
                        spec.has_read_receipt(round.get("input_messages") or []) for round in post_resume_rounds
                    )
                else:
                    observation["rebuild"] = {"performed": False}
                    observation["post_resume_model_input_has_receipt"] = None
            else:
                record = await _wait_for_state(environment, task_id, TERMINAL_STATES, worker=worker)
                observation.update(await _observation_base(environment, task_id, gateway, registry, started))
                observation["approval"] = None
                observation["rebuild"] = {"performed": False}
                observation["post_resume_model_input_has_receipt"] = None

            observation["task_status"] = record.lifecycle_state.value
            result_extra = (record.extra or {}).get("result") or {}
            observation["final_content"] = result_extra.get("content")
            observation["error_category"] = result_extra.get("error_category")
            observation["error"] = result_extra.get("error")
            observation["reconciliation_reason"] = (record.extra or {}).get("reconciliation_reason")
            observation["model_calls"] = len(gateway.rounds)
            observation["write_attempts"] = registry.write_attempts
            observation["side_effect_count"] = registry.side_effects
            observation["usage"] = _usage(gateway.rounds)
            observation["collection_error"] = None
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = _fallback_observation(gateway, registry, started, exc)
        observation["usage"] = _usage(gateway.rounds)
    finally:
        pending = tuple(_background_tasks - previous_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        patch.unpatch()
        await environment.close()
    return observation


def _usage(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [
        round["usage"].get("total_tokens")
        for round in rounds
        if isinstance(round.get("usage"), dict) and isinstance(round["usage"].get("total_tokens"), int)
    ]
    measured = bool(rounds) and len(totals) == len(rounds)
    return {
        "source": "reported" if measured else "unverified",
        "token_total": sum(totals) if measured else None,
        "cost_amount": None,
        "currency": "USD",
        "price_version": "unverified",
    }
