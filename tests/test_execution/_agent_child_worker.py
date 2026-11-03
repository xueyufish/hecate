"""Independent worker process for the Agent child PostgreSQL fault matrix.

``python -m tests.test_execution._agent_child_worker <mode> <parent-task-id>
[<child-task-id>] [<extra>]`` — the database binds through the environment
(see ``_agent_child_db``), never through argv.

Modes
-----
``dispatch-parent``  one ``dispatch_once`` on the parent (creates the child, parks)
``dispatch-child``   one ``dispatch_once`` on the child
``drive``            ``reconcile()`` loop until the parent is terminal or the
                     time budget (``HECATE_AGENT_CHILD_MAX_SECONDS``) ends
``converge``         ``AgentChildRunReconciler.converge_once`` then dispatch the parent
``callback``         submit the verified child callback (``extra`` is the command id)

Fault points (``HECATE_AGENT_CHILD_FAULT``) — a hard ``os._exit(73)`` with no
cleanup, so nothing in memory survives and every surviving fact is durable:

``before_parent_park``          child submitted, the parent's park not committed
``after_child_submit``          child durably created, caller acceptance not settled
``after_parent_dispatch``       child created and parent parked, nothing else
``after_model_reserve``         the child's slice reserved for its model request,
                                before the model answered
``after_child_accept``          first accepted action batch committed (effect
                                settled), before the terminal path
``after_settlement_before_wake`` call budget settled, the parent wake not applied

Other knobs
-----------
``HECATE_AGENT_CHILD_SLOW_CHILD_MODEL``  seconds of BLOCKING sleep inside the child's
    model call: the loop stalls, the lease heartbeat dies — a hung executor,
    not a cooperative one.
``HECATE_AGENT_CHILD_LEASE_TTL``         dispatch lease TTL in seconds (default 60)
``HECATE_AGENT_CHILD_EFFECT_FILE``       JSONL, one line per executed ``web_search`` effect
``HECATE_AGENT_CHILD_MODEL_TRACE_FILE``  JSONL, one line per model request (parent/child)
``HECATE_AGENT_CHILD_CHILD_TOOL``        ``web_search`` wires the child's single tool effect
``HECATE_AGENT_CHILD_WORKSPACE_ID``      the harness workspace (``callback`` mode)
``HECATE_AGENT_CHILD_RELEASE_MODEL``     block the child model until this file exists
``HECATE_AGENT_CHILD_WAIT_CLOCK``        injected ISO expiry clock for the wait reconciler
``HECATE_AGENT_CHILD_INVALID_ANSWER``    return a value rejected by the child output schema

Only the provider boundary and the tool effect are doubles; everything else
is the real chain against the shared database.
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

DELEGATE = "delegate_agent"
EXIT_FAULT = 73
TERMINAL = ("succeeded", "failed", "cancelled", "reconciliation_required")


def _append(path_env: str, payload: dict[str, Any]) -> None:
    path = os.environ.get(path_env)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), **payload}) + "\n")


def _die(where: str) -> None:
    """Abrupt exit: no finally blocks, no pool disposal, no flushes beyond stdio."""

    _append("HECATE_AGENT_CHILD_MODEL_TRACE_FILE", {"fault": where})
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(EXIT_FAULT)


def _fault() -> str | None:
    return os.environ.get("HECATE_AGENT_CHILD_FAULT") or None


class _Delta:
    def __init__(self, index: int, call_id: str, name: str, arguments: str) -> None:
        self.index = index
        self.id = call_id
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=arguments)


class _LLM:
    """Parent delegates once then answers from the child's result; the child
    answers (tool-first when it carries a tool). Every request is traced."""

    def __init__(self) -> None:
        self.slow = float(os.environ.get("HECATE_AGENT_CHILD_SLOW_CHILD_MODEL") or 0)

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if DELEGATE in named:
            _append("HECATE_AGENT_CHILD_MODEL_TRACE_FILE", {"role": "parent"})
            child_results = [
                m for m in messages if m.get("role") == "user" and "child_task_id" in str(m.get("content"))
            ]
            if child_results:
                result = json.loads(child_results[-1]["content"])["provided_input"]
                yield {"content": json.dumps({"answer": f"child said {result['content']}"}), "tool_calls": None}
                return
            if not tool_messages:
                yield {
                    "content": "",
                    "tool_calls": [
                        _Delta(0, "call-1", DELEGATE, json.dumps({"alias": "worker", "input": {"task": "stock"}}))
                    ],
                }
                return
            if any("agent_child_refused" in str(m.get("content")) for m in tool_messages):
                yield {"content": json.dumps({"answer": "delegation refused"}), "tool_calls": None}
                return
            yield {"content": "", "tool_calls": None}
            return
        _append("HECATE_AGENT_CHILD_MODEL_TRACE_FILE", {"role": "child", "tools": named})
        release = os.environ.get("HECATE_AGENT_CHILD_RELEASE_MODEL")
        if release:
            # Deliberately block the event loop so the dispatch heartbeat
            # cannot renew while another process takes over its lease.
            end = time.monotonic() + 120
            while not Path(release).exists():
                if time.monotonic() >= end:
                    raise TimeoutError("test model release barrier was not opened")
                time.sleep(0.05)
        if self.slow:
            time.sleep(self.slow)  # deliberately blocking: a stalled executor
        if named and not tool_messages:
            yield {"content": "", "tool_calls": [_Delta(0, "call-1", "web_search", json.dumps({"query": "stock"}))]}
            return
        answer = 42 if os.environ.get("HECATE_AGENT_CHILD_INVALID_ANSWER") else f"child done by {os.getpid()}"
        yield {"content": json.dumps({"answer": answer}), "tool_calls": None}

    async def chat(self, **_kwargs):
        raise AssertionError("non-streaming chat must not be used by the matrix")


def _wire_doubles() -> None:
    import hecate_llm.service as llm_module

    llm_module.llm_service = _LLM()

    # Drive every task explicitly, exactly like the test process: an inline
    # background dispatch would die with this process and leave a stale lease.
    import hecate.execution.task_control as task_control

    task_control._spawn_background = lambda coro: coro.close()

    if os.environ.get("HECATE_AGENT_CHILD_CHILD_TOOL") == "web_search":
        import hecate.core.composition.entry_assembly as entry_assembly
        from hecate.tools.tool.builtin import BuiltInToolExecutor

        async def _load_tools(db, tool_names, *, workspace_id=None):
            # Faithful to the stored agent config: only an agent that declares
            # the tool sees it. A process-wide tool set would change the
            # PARENT's model-visible input between processes and trip the
            # (correct) decision-digest conflict on replay.
            if "web_search" not in (tool_names or []):
                return []
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

        entry_assembly.load_agent_tools = _load_tools
        real_execute = BuiltInToolExecutor.execute

        async def effect(self, name, args, context=None):
            if name != "web_search":
                return await real_execute(self, name, args, context)
            _append("HECATE_AGENT_CHILD_EFFECT_FILE", {"query": args.get("query")})
            return {"results": ["found"]}

        BuiltInToolExecutor.execute = effect


def _arm_fault(store: Any) -> None:
    fault = _fault()
    if fault in (None, "after_parent_dispatch"):
        return
    if fault == "after_child_submit":
        from hecate.execution.task_control import TaskControlService

        real_submit = TaskControlService.submit

        async def submit_then_die(self, *args, **kwargs):
            result = await real_submit(self, *args, **kwargs)
            if kwargs.get("_verified_agent_child") is True:
                _die(fault)
            return result

        TaskControlService.submit = submit_then_die
        return
    if fault == "after_model_reserve":
        from hecate_governance.usage import ReservationStatus

        from hecate.execution.agent_child import DurableSliceLedger

        real_reserve = DurableSliceLedger.reserve

        async def reserve_then_die(self, key, units):
            outcome = await real_reserve(self, key, units)
            if outcome.status is ReservationStatus.RESERVED:
                _die(fault)
            return outcome

        DurableSliceLedger.reserve = reserve_then_die
        return
    if fault == "after_child_accept":
        from hecate_runtime.accepted_action_batch import AcceptedActionBatch

        real_run = AcceptedActionBatch.run

        async def run_then_die(self, **kwargs):
            result = await real_run(self, **kwargs)
            _die(fault)
            return result

        AcceptedActionBatch.run = run_then_die
        return
    if fault in ("before_parent_park", "after_settlement_before_wake"):
        from hecate_durable.contracts.durable import TaskLifecycleState

        real_apply = store.apply_task_state
        target_state = TaskLifecycleState.WAITING_INPUT if fault == "before_parent_park" else TaskLifecycleState.QUEUED

        def apply_or_die(task_ref, target, **kwargs):
            wait = (kwargs.get("extra_update") or {}).get("wait") or {}
            if target is target_state and wait.get("wake_kind") == "agent_child":
                _die(fault)
            return real_apply(task_ref, target, **kwargs)

        store.apply_task_state = apply_or_die
        return
    raise SystemExit(f"unknown fault point {fault!r}")


def _terminal(state: Any) -> bool:
    return state.value in TERMINAL


async def _children(store: Any, parent_id: str) -> list[dict[str, Any]]:
    rows = []
    for task in await asyncio.to_thread(store.list_tasks):
        if task.task_ref.id == parent_id:
            continue
        payload = await asyncio.to_thread(store.get_task_input, task.task_ref) or {}
        stamp = payload.get("agent_child_parent")
        if isinstance(stamp, dict) and (stamp.get("task_ref") or {}).get("id") == parent_id:
            rows.append({"id": task.task_ref.id, "state": task.lifecycle_state.value, "call_id": stamp.get("call_id")})
    return rows


async def main() -> None:
    mode, parent_id = sys.argv[1], sys.argv[2]
    child_id = sys.argv[3] if len(sys.argv) > 3 else None
    extra = sys.argv[4] if len(sys.argv) > 4 else None
    _wire_doubles()

    from hecate_durable.storage.lease import StaleFenceError
    from hecate_durable.worker import DurableWorker

    import tests.conftest  # noqa: F401 - register the same platform ORM mappings
    from hecate.execution.agent_child_wake import AgentChildRunReconciler
    from hecate.execution.task_control import task_ref_of
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from tests.test_execution._agent_child_db import backend_name, open_store_and_sessions

    store, engine, sessions = open_store_and_sessions()
    _arm_fault(store)
    ttl = float(os.environ.get("HECATE_AGENT_CHILD_LEASE_TTL") or 60.0)
    parent_ref = task_ref_of(parent_id)
    clock_value = os.environ.get("HECATE_AGENT_CHILD_WAIT_CLOCK")
    clock = None
    if clock_value:
        from datetime import datetime
        from functools import partial

        clock = partial(datetime.fromisoformat, clock_value)
    reconciler = AgentChildRunReconciler(store, sessions, clock=clock)
    worker = DurableWorker(
        store,
        PlatformTaskDispatcher(store, sessions),
        leases=store.leases,
        lease_ttl=ttl,
        poll_interval=0.2,
        wait_reconciler=reconciler.converge_once,
    )
    report: dict[str, Any] = {"mode": mode, "pid": os.getpid(), "backend": backend_name(), "fenced": 0, "errors": []}

    async def dispatch(ref: Any) -> bool | None:
        try:
            return await worker.dispatch_once(ref)
        except StaleFenceError as exc:
            report["fenced"] += 1
            report["errors"].append(f"StaleFenceError: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 — the matrix records, the test judges
            report["errors"].append(f"{type(exc).__name__}: {exc}")
            return None

    try:
        if mode == "dispatch-parent":
            report["dispatched"] = await dispatch(parent_ref)
            if _fault() == "after_parent_dispatch":
                _die("after_parent_dispatch")
        elif mode == "dispatch-child":
            report["dispatched"] = await dispatch(task_ref_of(child_id))
        elif mode == "drive":
            deadline = time.monotonic() + float(os.environ.get("HECATE_AGENT_CHILD_MAX_SECONDS") or 60.0)
            report["cycles"] = report["dispatches"] = 0
            while time.monotonic() < deadline:
                try:
                    report["dispatches"] += await worker.reconcile()
                except StaleFenceError as exc:
                    report["fenced"] += 1
                    report["errors"].append(f"StaleFenceError: {exc}")
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append(f"{type(exc).__name__}: {exc}")
                report["cycles"] += 1
                parent = await asyncio.to_thread(store.get_task_state, parent_ref)
                children = await _children(store, parent_id)
                # Converged means the whole pair is terminal, not just the
                # parent: a cancelled parent's child still has to stop.
                if (
                    parent is not None
                    and _terminal(parent.lifecycle_state)
                    and all(child["state"] in TERMINAL for child in children)
                ):
                    break
                await asyncio.sleep(0.2)
        elif mode == "converge":
            report["recovered"] = await reconciler.converge_once()
            report["dispatched"] = await dispatch(parent_ref)
        elif mode == "callback":
            import uuid

            from hecate.execution.task_control import TaskControlService

            wait = ((await asyncio.to_thread(store.get_task_state, parent_ref)).extra or {}).get("wait") or {}
            async with sessions() as db:
                service = TaskControlService(
                    db, store=store, recorder=store, backend="postgres", ledger_source="core", session_factory=sessions
                )
                receipt = await service.submit_agent_child_callback(
                    workspace_id=uuid.UUID(os.environ["HECATE_AGENT_CHILD_WORKSPACE_ID"]),
                    task_id=uuid.UUID(parent_id),
                    command_id=extra or f"cb-{os.getpid()}",
                    wait_token=str(wait.get("wait_token")),
                    child_task_id=uuid.UUID(str(child_id)),
                    declared_state="succeeded",
                )
            report["receipt_state"] = receipt.record.state.value
        else:
            raise SystemExit(f"unknown mode {mode!r}")

        parent = await asyncio.to_thread(store.get_task_state, parent_ref)
        report["parent_state"] = parent.lifecycle_state.value if parent else None
        parent_extra = (parent.extra or {}) if parent else {}
        report["parent_reconciliation_reason"] = parent_extra.get("reconciliation_reason")
        report["parent_error"] = (parent_extra.get("result") or {}).get("error")
        report["children"] = await _children(store, parent_id)
        print(json.dumps(report))
    finally:
        await engine.dispose()
        store.dispose()


if __name__ == "__main__":
    asyncio.run(main())
