"""Recover a platform timer wait in an independent interpreter over the same SQL file."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime

from hecate_durable.storage import SqlDurableStore
from hecate_durable.worker import DurableWorker
from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import tests.conftest  # noqa: F401 - register the same platform ORM mappings as the parent
from hecate.execution.task_control import task_ref_of
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from hecate.execution.timer_wait_wake import TimerWaitWaker
from tests.test_execution.test_controller_loop_dispatch import _ScriptedAssembly
from tests.test_execution.test_timer_wait import _TimerPollPort


class RecoveredPollPort(_TimerPollPort):
    """The external job has completed; only a fresh poll may execute after recovery."""

    async def tool_execute(self, name, args, context=None):
        """Return the completed job and count actual calls across the process boundary."""
        self.business_calls.append(dict(args))
        return {"status": "succeeded", "result": "job-done"}

    async def llm_invoke_structured(self, messages, config):
        """Refuse any attempt to re-ask the already committed first decision."""
        if not any(message.get("role") == "tool" for message in messages):
            raise AssertionError("recovery re-asked a committed decision")
        async for response in super().llm_invoke_structured(messages, config):
            yield response


async def main() -> None:
    """Wake the persisted timer, then drive a fresh worker to the recorded final result."""
    import uuid

    url, task_id, wake_time = sys.argv[1:]
    store = SqlDurableStore(url, source="platform")
    engine = create_async_engine(url.replace("sqlite:", "sqlite+aiosqlite:", 1))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    port = RecoveredPollPort()
    assembly = _ScriptedAssembly(port)
    assembly.patch()
    _BUILTIN_CLASSIFICATIONS["query_inventory"] = SideEffectClass.READONLY
    try:
        task_ref = task_ref_of(uuid.UUID(task_id))
        woken = await TimerWaitWaker(store, sessions, clock=lambda: datetime.fromisoformat(wake_time)).wake_once()
        worker = DurableWorker(store, PlatformTaskDispatcher(store, sessions), leases=store.leases)
        await worker.dispatch_once(task_ref)
        record = store.get_task_state(task_ref)
        print(
            json.dumps(
                {
                    "woken": woken,
                    "state": record.lifecycle_state.value,
                    "model_calls": port.model_calls,
                    "tool_calls": len(port.business_calls),
                }
            )
        )
    finally:
        assembly.unpatch()
        await engine.dispose()
        store.dispose()


if __name__ == "__main__":
    asyncio.run(main())
