"""End-to-end tests for multi-agent orchestration workflows.

Tests graph DSL compilation and Pregel execution with agent nodes
using mock workers (no real DB or LLM calls needed).
"""

from __future__ import annotations

import asyncio
import uuid

from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import parse_graph
from hecate.engine.pregel import PregelRuntime
from hecate.engine.types import StreamMode
from hecate.services.workflow.test_runner import _TestWorker


def test_multi_agent_linear_execution():
    """Two agent nodes connected linearly execute in order via mock test run."""
    agent_a_id = str(uuid.uuid4())
    agent_b_id = str(uuid.uuid4())

    dsl = {
        "version": "1.0",
        "name": "linear-agents",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "router": {
                "type": "agent",
                "config": {
                    "agent_id": agent_a_id,
                    "channels": {
                        "readable": ["messages"],
                        "writable": ["messages"],
                    },
                },
            },
            "specialist": {
                "type": "agent",
                "config": {
                    "agent_id": agent_b_id,
                    "channels": {
                        "readable": ["messages"],
                        "writable": ["messages"],
                    },
                },
            },
        },
        "edges": [
            {"source": "router", "target": "specialist"},
            {"source": "specialist", "target": "__end__"},
        ],
        "entry": "router",
    }

    graph_config = parse_graph(dsl)
    compiled = GraphCompiler().compile(graph_config)

    worker = _TestWorker(
        mock=True,
        node_types={nid: ncfg.type for nid, ncfg in compiled.nodes.items()},
    )
    checkpoint_store = InMemoryCheckpointStore()
    session_id = uuid.uuid4()

    runtime = PregelRuntime(graph=compiled, worker=worker, checkpoint_store=checkpoint_store)

    async def _run():
        events = []
        async for event in runtime.execute(
            session_id=session_id,
            initial_input={"messages": [{"role": "user", "content": "hello"}]},
            stream_mode=StreamMode.UPDATES,
        ):
            events.append(event)
        return events

    events = asyncio.get_event_loop().run_until_complete(_run())

    assert len(events) >= 2
    executed_nodes = [e.get("node") for e in events if e.get("type") == "update"]
    assert executed_nodes[0] == "router"
    assert executed_nodes[1] == "specialist"


def test_multi_agent_with_handoff_edges():
    """Graph with handoff edges compiles and validates."""
    agent_a_id = str(uuid.uuid4())
    agent_b_id = str(uuid.uuid4())

    dsl = {
        "version": "1.0",
        "name": "handoff-test",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "triage": {
                "type": "agent",
                "config": {"agent_id": agent_a_id},
            },
            "billing": {
                "type": "agent",
                "config": {"agent_id": agent_b_id},
            },
        },
        "edges": [
            {"source": "__start__", "target": "triage"},
            {"source": "triage", "target": "billing", "type": "handoff"},
            {"source": "billing", "target": "__end__"},
        ],
        "entry": "triage",
    }

    graph_config = parse_graph(dsl)
    compiled = GraphCompiler().compile(graph_config)

    assert compiled is not None
    assert len(compiled.nodes) == 2
    handoff_edges = [e for e in compiled.edges if e.trigger == "handoff"]
    assert len(handoff_edges) == 1


def test_multi_agent_mixed_node_types():
    """Graph with agent, conversation, and condition nodes executes correctly."""
    agent_id = str(uuid.uuid4())

    dsl = {
        "version": "1.0",
        "name": "mixed-nodes",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "start": {
                "type": "conversation",
                "config": {"model": "gpt-4o", "system_prompt": "Start conversation"},
            },
            "assistant": {
                "type": "agent",
                "config": {"agent_id": agent_id},
            },
            "check": {
                "type": "condition",
                "config": {"expression": "true"},
            },
        },
        "edges": [
            {"source": "start", "target": "assistant"},
            {"source": "assistant", "target": "check"},
            {"source": "check", "target": {"true": "__end__", "false": "start"}},
        ],
        "entry": "start",
    }

    graph_config = parse_graph(dsl)
    compiled = GraphCompiler().compile(graph_config)

    worker = _TestWorker(
        mock=True,
        node_types={nid: ncfg.type for nid, ncfg in compiled.nodes.items()},
    )
    checkpoint_store = InMemoryCheckpointStore()
    session_id = uuid.uuid4()

    runtime = PregelRuntime(graph=compiled, worker=worker, checkpoint_store=checkpoint_store)

    async def _run():
        events = []
        async for event in runtime.execute(
            session_id=session_id,
            initial_input={"messages": [{"role": "user", "content": "test"}]},
            stream_mode=StreamMode.UPDATES,
        ):
            events.append(event)
        return events

    events = asyncio.get_event_loop().run_until_complete(_run())

    executed_nodes = [e.get("node") for e in events if e.get("type") == "update"]
    assert "start" in executed_nodes
    assert "assistant" in executed_nodes
