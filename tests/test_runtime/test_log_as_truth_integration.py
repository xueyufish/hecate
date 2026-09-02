"""End-to-end integration tests for log-as-truth execution state."""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.command import Command
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.context import InMemoryContextEngine
from hecate.runtime.eventstore import (
    CURRENT_LOG_SCHEMA_VERSION,
    EventType,
    InMemoryEventStore,
)
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.types import (
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
)
from hecate.runtime.worker import Worker, WorkerResult


class _EchoWorker(Worker):
    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        messages = list(channel_snapshot.get("messages", []))
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": messages + [{"role": "assistant", "content": f"echo:{node_id}"}]},
        )


class _InterruptWorker(Worker):
    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        return WorkerResult(
            node_id=node_id,
            command=Command(interrupt={"ask": "approve?"}),
        )


def _build_linear_graph() -> GraphConfig:
    return GraphConfig(
        name="wal_test",
        nodes={
            "a": NodeConfig(id="a", type=NodeType.CONVERSATION, config={}),
            "b": NodeConfig(id="b", type=NodeType.CONVERSATION, config={}),
        },
        edges=[
            Edge(source="a", target="b"),
            Edge(source="b", target="__end__"),
        ],
        entry="a",
    )


@pytest.mark.asyncio
async def test_pregel_emits_value_carrying_channel_writes_and_step_end():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    runtime = PregelRuntime(
        graph=GraphCompiler().compile(_build_linear_graph()),
        worker=_EchoWorker(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_store=store,
        context_engine=InMemoryContextEngine(),
    )

    async for _ in runtime.execute(
        session_id=sid,
        initial_input={"messages": [{"role": "user", "content": "hi"}]},
        stream_mode="values",
        execution_mode="conversational",
    ):
        pass

    persisted = await store.get_events(sid)
    channel_write_events = [e for e in persisted if e.event_type == EventType.CHANNEL_WRITE]
    step_end_events = [e for e in persisted if e.event_type == EventType.STEP_END]

    assert len(channel_write_events) >= 2
    for event in channel_write_events:
        assert event.payload["log_schema_version"] == CURRENT_LOG_SCHEMA_VERSION
        assert event.payload["channel"] == "messages"
        assert "value" in event.payload
    assert len(step_end_events) == 2


@pytest.mark.asyncio
async def test_pregel_skips_underscore_control_channels_in_batch():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    runtime = PregelRuntime(
        graph=GraphCompiler().compile(_build_linear_graph()),
        worker=_EchoWorker(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_store=store,
        context_engine=InMemoryContextEngine(),
    )

    async for _ in runtime.execute(
        session_id=sid,
        initial_input={
            "messages": [{"role": "user", "content": "hi"}],
            "_session_id": str(sid),
            "_tools": [],
            "sys.execution_mode": "conversational",
        },
        stream_mode="values",
        execution_mode="conversational",
    ):
        pass

    persisted = await store.get_events(sid)
    channel_write_keys = {e.payload["channel"] for e in persisted if e.event_type == EventType.CHANNEL_WRITE}
    assert "_session_id" not in channel_write_keys
    assert "_tools" not in channel_write_keys
    assert "sys.execution_mode" not in channel_write_keys
    assert "messages" in channel_write_keys


@pytest.mark.asyncio
async def test_pregel_interrupt_records_interrupt_event_and_checkpoint():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    runtime = PregelRuntime(
        graph=GraphCompiler().compile(_build_linear_graph()),
        worker=_InterruptWorker(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_store=store,
        context_engine=InMemoryContextEngine(),
    )

    async for event in runtime.execute(
        session_id=sid,
        initial_input={"messages": [{"role": "user", "content": "hi"}]},
        stream_mode="values",
        execution_mode="conversational",
    ):
        if event.get("type") == "interrupt":
            break

    persisted = await store.get_events(sid)
    interrupt_events = [e for e in persisted if e.event_type == EventType.INTERRUPT]
    assert len(interrupt_events) == 1
    assert interrupt_events[0].payload["interrupt_value_type"] == "dict"
    assert await store.get_version(sid) > 0
