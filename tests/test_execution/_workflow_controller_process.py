"""Independent worker process for workflow controller-step PostgreSQL tests.

``python -m tests.test_execution._workflow_controller_process <mode>
<parent-task-id> [<child-task-id>]`` — the database binds through the
environment (``_agent_child_db``), never through argv.

Modes
-----
``dispatch-parent``    one ``dispatch_once`` on the workflow parent (submits step 0, parks)
``dispatch-child``     one ``dispatch_once`` on the step child
``drive``              ``reconcile()`` loop until the parent and every child are terminal
``converge-callback``  the relay-shaped lost-callback reissue, raced behind a shared
                       ready-file barrier so concurrent processes issue the
                       verified callback AT the same boundary

Fault points (``HECATE_WF_CONTROLLER_FAULT``) — a hard ``os._exit(73)``:
``after_step_terminal``  the child committed its terminal fact, the verified
                         callback not yet issued

Knobs
-----
``HECATE_WF_CONTROLLER_RELEASE_TOOL``  the step's web_search effect BLOCKS until this
    file exists (after appending its "entered" line — the barrier proof that
    the worker reached the effect boundary)
``HECATE_WF_CONTROLLER_EFFECT_FILE``   JSONL, one line per effect boundary event
``HECATE_WF_CONTROLLER_MODEL_TRACE_FILE`` JSONL, one line per model request
``HECATE_WF_CONTROLLER_CHILD_TOOL``    ``web_search`` wires the step's single tool effect
``HECATE_WF_CONTROLLER_READY_DIR``     barrier directory for ``converge-callback``
``HECATE_WF_CONTROLLER_WORKSPACE_ID``  the harness workspace
``HECATE_WF_CONTROLLER_MAX_SECONDS``   ``drive`` time budget (default 60)

Only the provider boundary and the tool effect are doubles.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

EXIT_FAULT = 73
TERMINAL = ("succeeded", "failed", "cancelled", "reconciliation_required")


def _append(path_env: str, payload: dict[str, Any]) -> None:
    path = os.environ.get(path_env)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), **payload}) + "\n")


def _die(where: str) -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(EXIT_FAULT)


def _fault() -> str | None:
    return os.environ.get("HECATE_WF_CONTROLLER_FAULT") or None


class _StepLLM:
    """Controller steps stream a tool round then answer; chat steps answer
    directly. Every request is traced."""

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        named = (
            sorted({t["function"]["name"] for t in tools if isinstance(t, dict) and t.get("function", {}).get("name")})
            if tools
            else []
        )
        prompt = str([m for m in messages if isinstance(m, dict) and m.get("role") == "user"][:1])
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if "web_search" in named and not tool_messages and "research" in prompt:
            _append("HECATE_WF_CONTROLLER_MODEL_TRACE_FILE", {"role": "child", "turn": "tool"})
            yield {"content": "", "tool_calls": [_delta(0, "call-1", "web_search", {"query": "stock"})]}
            return
        _append("HECATE_WF_CONTROLLER_MODEL_TRACE_FILE", {"role": "child", "turn": "answer"})
        yield {"content": json.dumps({"report": "child done"}), "tool_calls": None}

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        _append("HECATE_WF_CONTROLLER_MODEL_TRACE_FILE", {"role": "child", "turn": "chat"})
        return SimpleNamespace(
            content="summary done",
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )


def _delta(index: int, call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _wire_doubles() -> None:
    import hecate_llm.service as llm_module

    llm_module.llm_service = _StepLLM()

    import hecate.execution.task_control as task_control

    task_control._spawn_background = lambda coro: coro.close()

    if os.environ.get("HECATE_WF_CONTROLLER_CHILD_TOOL") == "web_search":
        import hecate.core.composition.entry_assembly as entry_assembly
        from hecate.tools.tool.builtin import BuiltInToolExecutor

        async def _load_tools(db, tool_names, *, workspace_id=None):
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
            _append("HECATE_WF_CONTROLLER_EFFECT_FILE", {"event": "entered"})
            release = os.environ.get("HECATE_WF_CONTROLLER_RELEASE_TOOL")
            if release:
                end = time.monotonic() + 120
                while not Path(release).exists():
                    if time.monotonic() >= end:
                        raise TimeoutError("test tool release barrier was not opened")
                    await asyncio.sleep(0.05)
            _append("HECATE_WF_CONTROLLER_EFFECT_FILE", {"event": "executed"})
            return {"results": ["found"]}

        BuiltInToolExecutor.execute = effect

    if _fault() == "after_step_terminal":
        from hecate.execution.task_dispatcher import PlatformTaskDispatcher

        async def die_before_callback(self, db, context, outcome, *, retry_errors=False):
            _die(_fault())

        PlatformTaskDispatcher._issue_child_callback = die_before_callback
        return


def _arm_fault(store: Any) -> None:
    if _fault() not in (None, "after_step_terminal"):
        raise SystemExit(f"unknown fault point {_fault()!r}")


async def _children(store: Any, parent_id: str) -> list[dict[str, Any]]:
    rows = []
    for task in await asyncio.to_thread(store.list_tasks):
        if task.task_ref.id == parent_id:
            continue
        payload = await asyncio.to_thread(store.get_task_input, task.task_ref) or {}
        stamp = payload.get("workflow_parent")
        if isinstance(stamp, dict) and (stamp.get("task_ref") or {}).get("id") == parent_id:
            rows.append({"id": task.task_ref.id, "state": task.lifecycle_state.value, "step": stamp.get("step_index")})
    return rows


async def main() -> None:
    mode, parent_id = sys.argv[1], sys.argv[2]
    child_id = sys.argv[3] if len(sys.argv) > 3 else None
    _wire_doubles()
    _arm_fault(None)

    from hecate_durable.storage.lease import StaleFenceError
    from hecate_durable.worker import DurableWorker

    import tests.conftest  # noqa: F401 - register the same platform ORM mappings
    from hecate.execution.task_control import TaskControlService, task_ref_of
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from tests.test_execution._agent_child_db import backend_name, open_store_and_sessions

    store, engine, sessions = open_store_and_sessions()
    parent_ref = task_ref_of(parent_id)
    worker = DurableWorker(
        store,
        PlatformTaskDispatcher(store, sessions),
        leases=store.leases,
        poll_interval=0.2,
    )
    report: dict[str, Any] = {"mode": mode, "pid": os.getpid(), "backend": backend_name(), "errors": []}

    async def dispatch(ref: Any) -> bool | None:
        try:
            return await worker.dispatch_once(ref)
        except StaleFenceError as exc:
            report["errors"].append(f"StaleFenceError: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 — the test judges the durable outcome
            report["errors"].append(f"{type(exc).__name__}: {exc}")
            return None

    try:
        if mode == "dispatch-parent":
            report["dispatched"] = await dispatch(parent_ref)
        elif mode == "dispatch-child":
            report["dispatched"] = await dispatch(task_ref_of(child_id))
        elif mode == "drive":
            deadline = time.monotonic() + float(os.environ.get("HECATE_WF_CONTROLLER_MAX_SECONDS") or 60.0)
            report["cycles"] = report["dispatches"] = 0
            while time.monotonic() < deadline:
                try:
                    report["dispatches"] += await worker.reconcile()
                except StaleFenceError as exc:
                    report["errors"].append(f"StaleFenceError: {exc}")
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append(f"{type(exc).__name__}: {exc}")
                report["cycles"] += 1
                parent = await asyncio.to_thread(store.get_task_state, parent_ref)
                children = await _children(store, parent_id)
                if (
                    parent is not None
                    and parent.lifecycle_state.value in TERMINAL
                    and all(child["state"] in TERMINAL for child in children)
                ):
                    break
                await asyncio.sleep(0.2)
        elif mode == "converge-callback":
            # Barrier: both racing processes load the child's committed
            # terminal fact FIRST, then issue the verified callback at the
            # same boundary — a sequential run cannot produce this race.
            child_state = await asyncio.to_thread(store.get_task_state, task_ref_of(child_id))
            assert child_state.lifecycle_state.value == "succeeded", child_state.extra
            ready = os.environ.get("HECATE_WF_CONTROLLER_READY_DIR")
            if ready:
                # Race mode: both processes load the child's committed facts
                # FIRST, then issue the callback at the same boundary.
                ready_dir = Path(ready)
                (ready_dir / f"ready-{os.getpid()}").touch()
                end = time.monotonic() + 60
                while len(list(ready_dir.glob("ready-*"))) < 2:
                    if time.monotonic() >= end:
                        raise TimeoutError("the second callback racer never reached the barrier")
                    await asyncio.sleep(0.05)
            task_input = await asyncio.to_thread(store.get_task_input, task_ref_of(child_id))
            async with sessions() as db:
                service = TaskControlService(
                    db, store=store, recorder=store, backend="postgres", ledger_source="core", session_factory=sessions
                )
                parent_state = await asyncio.to_thread(store.get_task_state, parent_ref)
                token = str(((parent_state.extra or {}).get("wait") or {}).get("wait_token"))
                receipt = await service.submit_workflow_callback(
                    workspace_id=uuid.UUID(os.environ["HECATE_WF_CONTROLLER_WORKSPACE_ID"]),
                    task_id=uuid.UUID(parent_id),
                    command_id=f"wf-callback-{child_id}-{token}",
                    wait_token=token,
                    child_task_id=uuid.UUID(child_id),
                    declared_state="succeeded",
                    payload={"step_index": (task_input.get("workflow_parent") or {}).get("step_index")},
                )
            report["receipt_state"] = receipt.record.state.value
            report["receipt_detail"] = receipt.detail
        else:
            raise SystemExit(f"unknown mode {mode!r}")

        parent = await asyncio.to_thread(store.get_task_state, parent_ref)
        report["parent_state"] = parent.lifecycle_state.value if parent else None
        report["children"] = await _children(store, parent_id)
        print(json.dumps(report))
    finally:
        await engine.dispose()
        store.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        # The full traceback survives on disk: pytest's assertion repr
        # truncates long stderr strings, hiding the failing frame.
        import traceback as _tb

        error_path = os.environ.get("HECATE_WF_CONTROLLER_ERROR_FILE")
        if error_path:
            with open(error_path, "w", encoding="utf-8") as handle:
                _tb.print_exc(file=handle)
        raise
