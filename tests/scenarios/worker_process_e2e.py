"""Worker subprocess body for the durable-approval restart E2E (step5e).

Runs as ``python -m tests.scenarios.worker_process_e2e``: one REAL worker
process wired against the shared PostgreSQL (platform ORM + durable store +
session-state snapshots). Only the provider boundary is scripted — the
dispatcher, engine, approval gate, ledger, and every persistence fact are
the production chain, in a separate OS process that the test parent kills
and replaces. This module is the process body, not a pytest module.

Environment:

- ``DATABASE_URL`` — async PostgreSQL URL (platform ORM + session state).
- ``HECATE_E2E_DURABLE_DSN`` — sync psycopg URL for the durable store.
- ``HECATE_E2E_COUNTER_FILE`` — append-only side-effect counter shared by
  the synthetic tool across processes.
- ``HECATE_E2E_IDLE_SECONDS`` — exit once no task is claimable for this
  long (crash simulation is the parent's kill, not this path).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace


def _wire_synthetic_tool() -> None:
    """Register the protected synthetic tool and its write classification."""

    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS

    BUILTIN_TOOL_DEFINITIONS["stub_ticket"] = {
        "name": "stub_ticket",
        "description": "Create a ticket (synthetic restart-E2E tool)",
        "risk_level": "HIGH",
        "approval_required": True,
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        "returns": {"type": "string"},
    }
    _BUILTIN_CLASSIFICATIONS["stub_ticket"] = SideEffectClass.NON_IDEMPOTENT_WRITE
    BUILTIN_TOOL_DEFINITIONS["stub_prepare"] = {
        **BUILTIN_TOOL_DEFINITIONS["stub_ticket"],
        "name": "stub_prepare",
        "approval_required": False,
    }
    _BUILTIN_CLASSIFICATIONS["stub_prepare"] = SideEffectClass.NON_IDEMPOTENT_WRITE


class _TicketExecutor:
    """The synthetic side-effecting tool; counts executions cross-process."""

    async def execute(self, name: str, args: dict, context: dict | None = None) -> str:
        counter_file = os.environ["HECATE_E2E_COUNTER_FILE"]
        with open(counter_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tool": name, "args": args, "pid": os.getpid()}) + "\n")
        return f"{'PRE-1' if name == 'stub_prepare' else 'TCK-1'}: {args.get('title')}"


class _ScriptedProvider:
    """Deterministic provider: propose the protected call, then answer.

    Round-stable call id — a resumed attempt re-proposes the same logical
    call, which is what the binding digest and receipt replay key on. The
    engine consumes the STREAMING shape (the runtime port always drives
    ``chat_stream`` and accumulates litellm-style delta objects), so the
    tool proposal rides a streamed delta, not a completed response.
    """

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        receipts = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        mode = os.environ.get("HECATE_E2E_MODE", "normal")
        call_ids = {m.get("tool_call_id") for m in receipts}
        proposal = None
        if mode == "precompleted" and "call_prepare" not in call_ids:
            proposal = ("call_prepare", "stub_prepare")
        elif "call_1" not in call_ids:
            proposal = ("call_1", "stub_ticket")
        elif mode == "repeated" and "call_2" not in call_ids:
            proposal = ("call_2", "stub_ticket")
        if proposal is not None:
            return SimpleNamespace(
                content="",
                model=model or "stub-model",
                tool_calls=[
                    {
                        "id": proposal[0],
                        "type": "function",
                        "function": {"name": proposal[1], "arguments": json.dumps({"title": "fix the ladder"})},
                    }
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )
        return SimpleNamespace(
            content=json.dumps({"answer": "ticket done", "receipt": str(receipts[-1].get("content", ""))[:120]}),
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **kwargs):
        result = await self.chat(messages=messages, model=model, tools=tools, **kwargs)
        if result.tool_calls:
            # Mirrors the production litellm chunk: an attribute-shaped delta
            # the port accumulates by index (see _StreamToolDelta in the G3
            # entry tests).
            delta = SimpleNamespace(
                index=0,
                id=result.tool_calls[0]["id"],
                type="function",
                function=SimpleNamespace(**result.tool_calls[0]["function"]),
            )
            yield {"content": "", "tool_calls": [delta]}
            yield {"content": None, "tool_calls": None}
            return
        yield {"content": result.content, "tool_calls": None, "finish_reason": "stop"}
        yield {"content": None, "tool_calls": None}


class _ObservedProvider:
    """Record actual model inputs and outputs from each worker process."""

    def __init__(self, inner):
        self.inner = inner

    def _record(self, phase, **facts):
        path = os.environ["HECATE_E2E_MODEL_TRACE_FILE"]

        def encode(value):
            if hasattr(value, "model_dump"):
                return value.model_dump()
            return vars(value) if hasattr(value, "__dict__") else str(value)

        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "phase": phase, **facts}, default=encode) + "\n")

    async def chat(self, **kwargs):
        self._record("input", messages=kwargs.get("messages"), model=kwargs.get("model"), tools=kwargs.get("tools"))
        result = await self.inner.chat(**kwargs)
        self._record("output", result=result)
        return result

    async def chat_stream(self, **kwargs):
        self._record("input", messages=kwargs.get("messages"), model=kwargs.get("model"), tools=kwargs.get("tools"))
        output = []
        error = None
        try:
            async for chunk in self.inner.chat_stream(**kwargs):
                output.append(chunk)
                yield chunk
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            self._record("output", chunks=output, error_type=error)


def _wire_engine_doubles() -> None:
    """Script the provider and the tool registry inside THIS process.

    Everything else (dispatcher, engine, approval callback, ledger, stores)
    is the real production chain. With ``HECATE_E2E_REAL_MODEL=1`` the real
    ``hecate_llm`` service stays wired (the real-model acceptance variant):
    only the tool registry is overridden, so the model proposes the protected
    call itself and the park/wake/consume machinery is exercised unchanged.
    """

    import hecate_llm.service as llm_module

    from hecate.core.composition import entry_assembly
    from hecate.core.config import settings
    from hecate.tools.tool.registry import ToolRegistry

    # The engine tool loop ships default-off (G3 flip is a separate change);
    # the durable-approval park lives on that loop.
    settings.CHAT_TOOL_LOOP_ENGINE_ENABLED = True
    if os.environ.get("HECATE_E2E_REAL_MODEL") != "1":
        llm_module.llm_service = _ScriptedProvider()
    llm_module.llm_service = _ObservedProvider(llm_module.llm_service)

    if os.environ.get("HECATE_E2E_CRASH_AFTER_CONSUME") == "1":
        from pathlib import Path

        from hecate.execution.approval_wait import DurableBindingConsumer

        request_approval = DurableBindingConsumer.request_approval

        async def hold_after_consume(self, *args, **kwargs):
            decision = await request_approval(self, *args, **kwargs)
            Path(os.environ["HECATE_E2E_CONSUMED_FILE"]).write_text(decision.approval_ref, encoding="utf-8")
            await asyncio.Event().wait()
            return decision

        DurableBindingConsumer.request_approval = hold_after_consume

    def _build(db, skill_ref_manifest=None, *, workspace_id=None):
        registry = ToolRegistry(db=db, builtin_executor=_TicketExecutor(), workspace_id=workspace_id)
        registry._builtin_names = {"stub_ticket", "stub_prepare"}
        return registry

    entry_assembly.build_tool_registry = _build


async def _serve() -> None:
    from hecate_durable.storage import SqlDurableStore
    from hecate_durable.worker import DurableWorker, OutboxRelay

    from hecate.core.composition.entry_assembly import (
        register_shared_event_store,
        register_shared_registry_session_factory,
    )
    from hecate.core.database import async_session_factory
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from hecate.studio.event_state.postgres_store import PostgresEventStore

    # The production composition root registers these during app lifespan;
    # a bare worker process registers the same PG-backed stores so the
    # engine event log and the entry terminal writes are as real as the
    # rest of the chain.
    register_shared_event_store(PostgresEventStore(async_session_factory=async_session_factory))
    register_shared_registry_session_factory(async_session_factory)

    role = os.environ.get("HECATE_E2E_TASK_ROLE")

    class RoleStore(SqlDurableStore):
        """Let separate test processes own parent and child dispatches."""

        def list_tasks(self, states=None):
            records = super().list_tasks(states)
            if role is None:
                return records
            return [
                record
                for record in records
                if bool((self.get_task_input(record.task_ref) or {}).get("workflow_parent")) == (role == "child")
            ]

    if role is not None:
        import hecate.execution.task_control as task_control

        # Inline dispatch would bypass the process split the test measures.
        # Fleet workers, as in production's worker mode, own every claim.
        task_control._spawn_background = lambda coroutine: coroutine.close()

    store = RoleStore(os.environ["HECATE_E2E_DURABLE_DSN"], source="platform")
    dispatcher = PlatformTaskDispatcher(store, async_session_factory)
    worker = DurableWorker(
        store,
        dispatcher,
        leases=store.leases,
        lease_ttl=float(os.environ.get("HECATE_E2E_LEASE_TTL", "5")),
        poll_interval=0.2,
        max_attempts=3,
    )
    if os.environ.get("HECATE_E2E_PID_FILE"):
        from pathlib import Path

        Path(os.environ["HECATE_E2E_PID_FILE"]).write_text(str(os.getpid()), encoding="utf-8")
    print(f"worker ready pid={os.getpid()}", flush=True)
    # The parent kills this process (that IS the crash under test) or
    # terminates it after the terminal fact is asserted.
    from hecate.execution.event_relay import PlatformOutboxProjector

    relay = OutboxRelay(
        store.session_factory, PlatformOutboxProjector(store, async_session_factory), relay_key="platform"
    )
    await asyncio.gather(worker.run_forever(), relay.run_forever())


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _wire_synthetic_tool()
    _wire_engine_doubles()
    asyncio.run(_serve())


if __name__ == "__main__":
    sys.exit(main())
