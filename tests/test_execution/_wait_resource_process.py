"""Worker subprocess body for the managed wait-resource drill.

Runs as ``python -m tests.test_execution._wait_resource_process``: one REAL
worker process wired against the shared SQL database (platform ORM +
durable store + outbox relay over the SAME paths the test parent uses).
Only the provider and business-tool boundaries are scripted — the
dispatcher, controller loop, input/approval waits, ledger, commands and
every persistence fact are the production chain, in a separate OS process
the test parent kills and replaces. This module is a process body, never
imported by the test module.

Environment:

- ``HECATE_WAITRES_DSN`` — sync URL for the durable store.
- ``HECATE_WAITRES_DATABASE_URL`` — async URL for the platform ORM.
- ``HECATE_WAITRES_MODE`` — ``input``: the scripted wait the controller parks on.
- ``HECATE_WAITRES_TOOL_TRACE`` / ``HECATE_WAITRES_MODEL_TRACE`` —
  append-only JSONL evidence of real business executions and model asks.
- ``HECATE_WAITRES_READY_FILE`` / ``HECATE_WAITRES_PID_FILE`` — the parent's
  inter-process ready signal and process identity record.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hecate_runtime.ports import StubRuntimePort


def _append_trace(path: str, event: dict) -> None:
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid(), **event}, default=str) + "\n")


class _WaitResourcePort(StubRuntimePort):
    """Scripted controller port for one wait mode.

    ``input``: the business tool declares the explicit input-required
    contract on the first call (no side effect), then serves real data —
    the model completes the request with the provided region after the
    wake. ``approval``: the model proposes the governed call once; the
    approval wake consumes the binding and the same call executes.

    Every model ask and business execution is traced, so the parent can
    prove across process boundaries that committed decisions are never
    re-asked and completed actions never re-executed.
    """

    def __init__(self) -> None:
        super().__init__()
        self.mode = os.environ["HECATE_WAITRES_MODE"]
        self.tool_trace = os.environ["HECATE_WAITRES_TOOL_TRACE"]
        self.model_trace = os.environ["HECATE_WAITRES_MODEL_TRACE"]
        self.model_calls = 0
        self.business_calls = 0

    def _record_model(self) -> None:
        self.model_calls += 1
        _append_trace(self.model_trace, {"event": "model_ask", "mode": self.mode})

    def _propose(self, call_id: str, tool: str, arguments: dict) -> dict:
        return {
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}
            ],
        }

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self._record_model()
        tool_messages = [message for message in messages if message.get("role") == "tool"]
        provided = [
            json.loads(message["content"])["provided_input"]
            for message in messages
            if message.get("role") == "user" and "provided_input" in str(message.get("content"))
        ]
        first_user = next((message.get("content") for message in messages if message.get("role") == "user"), "")
        direct = isinstance(first_user, str) and "direct" in first_user
        if direct:
            yield {"content": json.dumps({"answer": "direct done"}), "tool_calls": None}
            return
        if not tool_messages:
            # A "direct" task completes without the input request: the same
            # worker serves it while the first task stays parked.
            yield self._propose("call-1", "query_inventory", {"sku": "S-9", **({"region": "EU"} if direct else {})})
        elif self.mode == "input" and len(tool_messages) == 1 and provided:
            yield self._propose("call-2", "query_inventory", {"sku": "S-9", "region": provided[-1]["region"]})
        elif self.mode == "timer" and len(tool_messages) == 1:
            yield self._propose("call-2", "query_inventory", {"sku": "S-9", "region": "EU"})
        else:
            yield {
                "content": json.dumps({"answer": "done", "region": provided[-1]["region"] if provided else "n/a"}),
                "tool_calls": None,
            }

    async def tool_execute(self, name: str, args: dict, context=None):
        self.business_calls += 1
        _append_trace(self.tool_trace, {"event": "business_call", "tool": name, "arguments": args})
        if self.mode == "input" and "region" not in args:
            return {"status": "input_required", "contract": {"fields": ["region"]}}
        if self.mode == "timer" and "region" not in args:
            return {"status": "timer_wait_required", "wait": {"version": 1, "wake_in_seconds": 300, "reason": "stock"}}
        return {"quantity": 3, "region": args.get("region", "n/a")}


def _wire(mode: str, port: _WaitResourcePort) -> None:
    """Patch the composition seams the controller assembly consumes.

    The same seams ``_ScriptedAssembly`` patches in-process; here the
    patches live for the whole child process lifetime. The approval mode
    keeps the production access policy and approval callback. The test supplies
    an ASK rule and the dispatcher wires its durable approval backend.
    """

    import hecate.core.composition.entry_assembly as entry_assembly
    import hecate.core.composition.guardrail_platform as guardrail_platform
    import hecate.core.composition.runtime_port_adapter as port_adapter

    entry_assembly.load_agent_tools = _load_tools
    entry_assembly.build_tool_registry = lambda *args, **kwargs: None
    entry_assembly.get_shared_event_store = lambda: None
    guardrail_platform.assemble_guardrails = _approval_guardrails if mode == "approval" else _no_guardrails
    port_adapter.create_runtime_port = lambda *args, **kwargs: port


async def _load_tools(db, tool_names, *, workspace_id=None):
    from tests.test_execution.test_controller_loop_dispatch import TOOL_DEFINITION

    return [TOOL_DEFINITION]


async def _no_guardrails(*args: Any, **kwargs: Any) -> None:
    return None


async def _approval_guardrails(*args: Any, **kwargs: Any) -> Any:
    """A bundle whose single rule asks for approval on the governed tool.

    The production fail-closed approval callback receives its durable
    answerer from the dispatcher. The production ``_wire_approval_backend`` wires the durable approval
    consumer when the task declares ``approval_wait`` — this fixture only
    supplies the ask rule that makes the accepted call request approval.
    """

    from hecate_runtime.security.approval import FailingClosedApprovalCallback
    from hecate_runtime.tool_access import RuleAction, ToolAccessPolicy, ToolRule

    return SimpleNamespace(
        access_policy=ToolAccessPolicy(),
        approval_callback=FailingClosedApprovalCallback(
            session_id=kwargs.get("session_id"),
            workspace_id=kwargs.get("workspace_id"),
            agent_id=kwargs.get("agent_id"),
        ),
        rules=[ToolRule(action=RuleAction.ASK, pattern="query_inventory")],
        middleware_chains=None,
        denial_tracker=None,
    )


async def _serve() -> None:
    from hecate_durable.worker import DurableWorker, OutboxRelay

    # Fleet-worker mode: the child owns every claim; inline dispatch from
    # the control plane would bypass the process split the drill measures.
    import hecate.execution.task_control as task_control_module
    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    task_control_module._spawn_background = lambda coroutine: coroutine.close()

    from hecate_durable.storage import SqlDurableStore
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.test_execution._agent_child_db import SCHEMA_ENV, durable_dsn

    store = SqlDurableStore(durable_dsn(), source="platform")
    url = make_url(durable_dsn())
    schema = os.environ.get(SCHEMA_ENV)
    async_url = url.set(drivername="postgresql+asyncpg" if schema else "sqlite+aiosqlite")
    engine = create_async_engine(async_url, connect_args={"server_settings": {"search_path": schema}} if schema else {})
    if schema:
        # Keep the same scoped URL for the production factory's database
        # check, translating libpq options only at the asyncpg driver boundary.
        @event.listens_for(engine.sync_engine, "do_connect")
        def _asyncpg_options(dialect, connection_record, args, parameters):
            parameters.pop("options", None)

    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    relay = OutboxRelay(
        store.session_factory,
        PlatformOutboxProjector(store, async_session_factory),
        relay_key="platform",
    )
    cycles = 0

    class ObservedWorker(DurableWorker):
        """Measure completed production cycles after dispatch cleanup and relay."""

        async def reconcile(self) -> int:
            """Publish process costs and resource ownership at a completed cycle."""
            nonlocal cycles
            dispatched = await super().reconcile()
            await relay.pump_once()
            cycles += 1
            _append_trace(
                os.environ["HECATE_WAITRES_CYCLE_TRACE"],
                {
                    "cycle": cycles,
                    "monotonic_seconds": time.monotonic(),
                    "cpu_seconds": time.process_time(),
                    "inflight": len(self._inflight),
                    "heartbeats": len(self._heartbeat_tasks),
                    "connections": store.engine.pool.checkedout() + engine.sync_engine.pool.checkedout(),
                    "model_calls": port.model_calls,
                    "tool_calls": port.business_calls,
                    "dispatched": dispatched,
                },
            )
            return dispatched

    dispatcher = PlatformTaskDispatcher(store, async_session_factory)
    worker = ObservedWorker(
        store,
        dispatcher,
        leases=store.leases,
        # The child's FIRST dispatch cold-loads the whole controller
        # assembly; a short TTL would expire mid-dispatch and fence the
        # executor before the park is ever reached.
        lease_ttl=float(os.environ.get("HECATE_WAITRES_LEASE_TTL", "30")),
        poll_interval=0.2,
        max_attempts=3,
        wait_reconciler=_wait_reconciler(store, async_session_factory),
    )
    mode = os.environ["HECATE_WAITRES_MODE"]
    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    _BUILTIN_CLASSIFICATIONS["query_inventory"] = SideEffectClass.READONLY
    port = _WaitResourcePort()
    _wire(mode, port)
    if os.environ.get("HECATE_WAITRES_PID_FILE"):
        Path(os.environ["HECATE_WAITRES_PID_FILE"]).write_text(str(os.getpid()), encoding="utf-8")
    ready = os.environ.get("HECATE_WAITRES_READY_FILE")
    if ready:
        Path(ready).write_text(str(os.getpid()), encoding="utf-8")
    print(f"wait-resource worker ready pid={os.getpid()} mode={mode}", flush=True)
    try:
        await worker.run_forever()
    finally:
        await worker.drain()
        await engine.dispose()
        store.dispose()


def _wait_reconciler(store, async_session_factory):
    """The composite wait reconciler, wired like the platform worker mode."""

    import hecate.core.database as database_module
    from hecate.execution.approval_wait_expiration import standalone_wait_reconcilers

    database_module.async_session_factory = async_session_factory
    return standalone_wait_reconcilers(store=store)


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(_serve())


if __name__ == "__main__":
    sys.exit(main())
