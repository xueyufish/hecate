"""Recover a lost Agent child callback in an independent interpreter.

Runs as ``python -m tests.test_execution._agent_child_process <db-url>
<parent-task-id>``: the process re-opens the same SQL database, converges
the parked parent whose child already reached a terminal state (the
callback was lost with the original process), and re-dispatches the
parent to completion. The provider boundary is the only stub — the
child's committed facts drive everything, and the child itself must never
re-execute.
"""

from __future__ import annotations

import asyncio
import json
import sys


def _wire_llm_stub() -> None:
    """The parent's controller turns only; the child must not re-run."""

    import hecate_llm.service as llm_module

    class _Stub:
        async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
            results = [m for m in messages if m.get("role") == "user" and "child_task_id" in str(m.get("content"))]
            if results:
                result = json.loads(results[-1]["content"])["provided_input"]
                yield {"content": json.dumps({"answer": f"recovered: {result['content']}"}), "tool_calls": None}
                return
            yield {"content": "", "tool_calls": None}

        async def chat(self, **_kwargs):
            raise AssertionError("the child must never re-execute during recovery")

    llm_module.llm_service = _Stub()


async def main() -> None:
    url, parent_task_id = sys.argv[1:]
    _wire_llm_stub()

    from hecate_durable.storage import SqlDurableStore
    from hecate_durable.worker import DurableWorker
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import tests.conftest  # noqa: F401 - register the same platform ORM mappings
    from hecate.execution.agent_child_wake import AgentChildRunReconciler
    from hecate.execution.task_control import task_ref_of
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    store = SqlDurableStore(url, source="platform")
    engine = create_async_engine(url.replace("sqlite:", "sqlite+aiosqlite:", 1))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        parent_ref = task_ref_of(parent_task_id)
        reconciler = AgentChildRunReconciler(store, sessions)
        recovered = await reconciler.converge_once()
        record = store.get_task_state(parent_ref)
        state_after_recovery = record.lifecycle_state.value
        worker = DurableWorker(store, PlatformTaskDispatcher(store, sessions), leases=store.leases)
        dispatched = await worker.dispatch_once(parent_ref)
        final = store.get_task_state(parent_ref)
        result = (final.extra or {}).get("result") or {}
        revisions = store.list_input_revisions(parent_ref)
        children = []
        for task in store.list_tasks():
            if task.task_ref.id == parent_task_id:
                continue
            payload = store.get_task_input(task.task_ref) or {}
            stamp = payload.get("agent_child_parent")
            if isinstance(stamp, dict) and (stamp.get("task_ref") or {}).get("id") == parent_task_id:
                children.append(stamp.get("call_id"))
        print(
            json.dumps(
                {
                    "recovered": recovered,
                    "state_after_recovery": state_after_recovery,
                    "dispatched": dispatched,
                    "state": final.lifecycle_state.value,
                    "content": result.get("content", ""),
                    "revisions": [row["source"] for row in revisions],
                    "child_call_ids": children,
                }
            )
        )
    finally:
        await engine.dispose()
        store.dispose()


if __name__ == "__main__":
    asyncio.run(main())
