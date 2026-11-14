"""Live-input drill worker with barriers after real persistence commits.

Run with ``python -m tests.test_execution._live_input_process``. Only the
model/tool seams are scripted. Barriers pause a model call or the return
from a committed input binding; they never write execution facts.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from hecate_runtime.ports import StubRuntimePort


def _trace(event: str, **fields) -> None:
    with open(os.environ["HECATE_LIVE_TRACE"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid(), "event": event, **fields}) + "\n")


async def _barrier(name: str) -> None:
    path = Path(os.environ["HECATE_LIVE_BARRIERS"]) / f"{name}.release"
    deadline = time.monotonic() + 180
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"barrier {name} was never released")
        await asyncio.sleep(0.05)


class StubLiveInputPort(StubRuntimePort):
    """Record synthetic per-turn inputs and business executions across workers."""

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        """Return two reads then a final answer, or a declared fault-test variant."""
        tools = [message for message in messages if message.get("role") == "tool"]
        turn = len(tools) + 1
        provided = [
            json.loads(message["content"])["provided_input"]
            for message in messages
            if message.get("role") == "user" and "provided_input" in str(message.get("content"))
        ]
        _trace("model_ask", turn=turn, provided=provided)
        if turn in json.loads(os.environ.get("HECATE_LIVE_HOLD_TURNS", "[]")):
            await _barrier(f"model-{turn}")
        mode = os.environ.get("HECATE_LIVE_MODE", "reads")
        if mode == "final" or (mode != "endless" and turn > 2):
            yield {"content": json.dumps({"answer": "done", "inputs": provided}), "tool_calls": None}
            return
        arguments = {"sku": "S-9", "turn": turn}
        if provided:
            arguments["region"] = provided[-1]["region"]
        yield {
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{turn}",
                    "type": "function",
                    "function": {"name": "query_inventory", "arguments": json.dumps(arguments)},
                }
            ],
        }

    async def tool_execute(self, name: str, args: dict, context=None):
        """Record one actual synthetic execution, distinct from its ledger receipt."""
        _trace("business_call", turn=args["turn"], region=args.get("region"))
        return {"quantity": 3, "region": args.get("region")}


async def _serve() -> None:
    from hecate_durable.worker import DurableWorker, OutboxRelay
    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    import hecate.execution.task_control as task_control
    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from tests.test_execution._agent_child_db import open_store_and_sessions
    from tests.test_execution.test_controller_loop_dispatch import _no_guardrails, _ScriptedAssembly

    task_control._spawn_background = lambda coroutine: coroutine.close()
    store, engine, sessions = open_store_and_sessions()
    port = StubLiveInputPort()
    assembly = _ScriptedAssembly(port, guardrails=_no_guardrails)
    assembly.patch()
    _BUILTIN_CLASSIFICATIONS["query_inventory"] = SideEffectClass.READONLY
    original_bind = store.bind_pending_input_revisions

    def observed_bind(*args, **kwargs):
        bound = original_bind(*args, **kwargs)
        if bound:
            _trace(
                "binding_committed",
                revisions=[
                    {"revision_id": row["revision_id"], "effective_turn": row["effective_turn"]} for row in bound
                ],
            )
            if os.environ.get("HECATE_LIVE_HOLD_BIND") == "1":
                release = Path(os.environ["HECATE_LIVE_BARRIERS"]) / "binding.release"
                deadline = time.monotonic() + 180
                while not release.is_file():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("binding barrier was never released")
                    time.sleep(0.05)
        return bound

    store.bind_pending_input_revisions = observed_bind
    relay = OutboxRelay(store.session_factory, PlatformOutboxProjector(store, sessions), relay_key="platform")
    cycles = 0

    class ObservedWorker(DurableWorker):
        """Publish observation points after actual worker cycles."""

        async def reconcile(self) -> int:
            """Run production recovery and expose its completed-cycle boundary."""
            nonlocal cycles
            dispatched = await super().reconcile()
            if dispatched and os.environ.get("HECATE_LIVE_HOLD_PROJECTION") == "1":
                await _barrier("projection")
            await relay.pump_once()
            cycles += 1
            _trace("cycle", cycle=cycles, inflight=len(self._inflight))
            return dispatched

    worker = ObservedWorker(
        store,
        PlatformTaskDispatcher(store, sessions),
        leases=store.leases,
        lease_ttl=15,
        poll_interval=0.1,
        max_attempts=3,
    )
    Path(os.environ["HECATE_LIVE_READY"]).write_text(str(os.getpid()), encoding="utf-8")
    try:
        await worker.run_forever()
    finally:
        await worker.drain()
        assembly.unpatch()
        await engine.dispose()
        store.dispose()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_serve())
