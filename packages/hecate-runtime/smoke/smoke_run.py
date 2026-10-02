"""Standalone-install smoke test for the ``hecate-runtime`` wheel (step5b).

Runs a three-node linear graph end-to-end using only the installed wheel:
a pass-through worker, the in-memory checkpoint store, and the Pregel
engine. Asserts the event sequence (one result per node, terminal
``values`` event) and that a checkpoint was persisted — proving the
kernel compiles, executes, checkpoints, and streams events without any
``hecate`` main-application code, repository source path, or optional
extras.

Usage (inside a clean venv where the wheel is installed):

    python smoke_run.py          # exit 0 on success, non-zero on failure
"""

from __future__ import annotations

import asyncio
import sys
import uuid


def main() -> int:
    try:
        import hecate_runtime  # noqa: F401
        from hecate_runtime.checkpoint import InMemoryCheckpointStore
        from hecate_runtime.pregel import PregelRuntime
        from hecate_runtime.types import (
            ChannelDef,
            ChannelType,
            CompiledGraph,
            Edge,
            NodeConfig,
            NodeType,
        )
        from hecate_runtime.worker import Worker, WorkerResult
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: kernel import error: {e}", file=sys.stderr)
        return 1

    # The kernel must not drag the main application in.
    for banned in list(sys.modules):
        if banned == "hecate" or banned.startswith("hecate."):
            print(f"FAIL: kernel import pulled in main-application module: {banned}", file=sys.stderr)
            return 2

    class EchoWorker(Worker):
        async def execute(
            self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
        ) -> WorkerResult:
            return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})

    graph = CompiledGraph(
        nodes={
            "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "smoke", "system_prompt": "A"}),
            "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={"model": "smoke", "system_prompt": "B"}),
            "C": NodeConfig(id="C", type=NodeType.CONVERSATION, config={"model": "smoke", "system_prompt": "C"}),
        },
        edges=[
            Edge(source="A", target="B"),
            Edge(source="B", target="C"),
            Edge(source="C", target="__end__"),
        ],
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point="A",
        name="smoke-linear",
    )

    async def run() -> list[dict]:
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, EchoWorker(), store)
        session_id = uuid.uuid4()
        events = []
        async for event in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            events.append(event)
        checkpoints = await store.list_checkpoints(session_id)
        if not checkpoints:
            raise RuntimeError("no checkpoint persisted")
        return events

    try:
        events = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: execution error: {e}", file=sys.stderr)
        return 3

    if len(events) != 3 or events[-1].get("type") != "values":
        print(f"FAIL: unexpected event sequence: {events}", file=sys.stderr)
        return 4

    caps_note = ""
    try:
        from hecate_runtime.capabilities_status import capability_status

        caps_note = f" capabilities={capability_status()}"
    except Exception as e:  # noqa: BLE001 — status report is best-effort
        print(f"(capability status unavailable: {e})")
    print(f"OK: 3-node linear execution, {len(events)} events, terminal values event.{caps_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
