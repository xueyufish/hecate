"""Tests for new node types: knowledge-retrieval and variable-set.

Covers parsing, compilation, and Pregel execution of graphs containing
the two new node types added in P2.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import parse_graph
from hecate.engine.pregel import PregelRuntime
from hecate.engine.types import (
    NodeType,
    StreamMode,
    WorkerResult,
)
from hecate.engine.worker import Worker

# --- DSL fixtures ---

KNOWLEDGE_RETRIEVAL_DSL = {
    "version": "1.0",
    "name": "knowledge-test",
    "state": {
        "messages": {"type": "topic", "default": []},
        "context": {"type": "last_value", "default": ""},
    },
    "nodes": {
        "retrieve": {
            "type": "knowledge-retrieval",
            "config": {"kb_ids": ["kb-001", "kb-002"], "query_template": "{{query}}", "top_k": 3},
        },
    },
    "edges": [{"source": "retrieve", "target": "__end__"}],
    "entry": "retrieve",
}

VARIABLE_SET_DSL = {
    "version": "1.0",
    "name": "variable-test",
    "state": {
        "messages": {"type": "topic", "default": []},
        "result": {"type": "last_value", "default": ""},
    },
    "nodes": {
        "set_var": {
            "type": "variable-set",
            "config": {"variable_name": "result", "value": "computed_value"},
        },
    },
    "edges": [{"source": "set_var", "target": "__end__"}],
    "entry": "set_var",
}

MIXED_DSL = {
    "version": "1.0",
    "name": "mixed",
    "state": {
        "messages": {"type": "topic", "default": []},
        "context": {"type": "last_value", "default": ""},
        "score": {"type": "last_value", "default": 0},
    },
    "nodes": {
        "retrieve": {
            "type": "knowledge-retrieval",
            "config": {"kb_ids": ["kb-1"], "query_template": "test"},
        },
        "score_var": {
            "type": "variable-set",
            "config": {"variable_name": "score", "value": 42},
        },
    },
    "edges": [{"source": "retrieve", "target": "score_var"}],
    "entry": "retrieve",
}


class _KnowledgeWorker(Worker):
    """Worker that simulates knowledge-retrieval: returns retrieved docs."""

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "messages": [{"role": "system", "content": "Retrieved 3 docs"}],
                "context": "knowledge retrieval result",
            },
        )


class _VariableWorker(Worker):
    """Worker that simulates variable-set: writes a channel value."""

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        var_name = node_config.get("variable_name", "unknown")
        var_value = node_config.get("value", "")
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [], var_name: var_value},
        )


class _MixedWorker(Worker):
    """Worker that routes based on node type via config inspection."""

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        if "kb_ids" in node_config:
            return WorkerResult(
                node_id=node_id,
                channel_updates={
                    "messages": [{"role": "system", "content": "Retrieved docs"}],
                    "context": "retrieved",
                },
            )
        if "variable_name" in node_config:
            var_name = node_config["variable_name"]
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], var_name: node_config.get("value")},
            )
        return WorkerResult(node_id=node_id, channel_updates={"messages": []})


# --- Parsing tests ---


def test_parse_knowledge_retrieval_node() -> None:
    """Knowledge-retrieval node type parses without error."""
    config = parse_graph(KNOWLEDGE_RETRIEVAL_DSL)
    assert config.nodes["retrieve"].type == NodeType.KNOWLEDGE_RETRIEVAL
    assert config.nodes["retrieve"].config["kb_ids"] == ["kb-001", "kb-002"]
    assert config.nodes["retrieve"].config["top_k"] == 3


def test_parse_variable_set_node() -> None:
    """Variable-set node type parses without error."""
    config = parse_graph(VARIABLE_SET_DSL)
    assert config.nodes["set_var"].type == NodeType.VARIABLE_SET
    assert config.nodes["set_var"].config["variable_name"] == "result"
    assert config.nodes["set_var"].config["value"] == "computed_value"


def test_parse_mixed_node_types() -> None:
    """Graph with both new types parses correctly."""
    config = parse_graph(MIXED_DSL)
    assert len(config.nodes) == 2
    assert config.nodes["retrieve"].type == NodeType.KNOWLEDGE_RETRIEVAL
    assert config.nodes["score_var"].type == NodeType.VARIABLE_SET


# --- Compilation tests ---


def test_compile_knowledge_retrieval() -> None:
    """Knowledge-retrieval graph compiles to a valid CompiledGraph."""
    config = parse_graph(KNOWLEDGE_RETRIEVAL_DSL)
    compiled = GraphCompiler().compile(config)
    assert compiled.entry_point == "retrieve"
    assert "retrieve" in compiled.nodes


def test_compile_variable_set() -> None:
    """Variable-set graph compiles to a valid CompiledGraph."""
    config = parse_graph(VARIABLE_SET_DSL)
    compiled = GraphCompiler().compile(config)
    assert compiled.entry_point == "set_var"
    assert "set_var" in compiled.nodes


def test_compile_mixed() -> None:
    """Mixed graph compiles with both new types."""
    config = parse_graph(MIXED_DSL)
    compiled = GraphCompiler().compile(config)
    assert len(compiled.nodes) == 2
    assert compiled.entry_point == "retrieve"


# --- Execution tests ---


@pytest.mark.asyncio
async def test_execute_knowledge_retrieval() -> None:
    """Knowledge-retrieval node executes and produces channel updates."""
    config = parse_graph(KNOWLEDGE_RETRIEVAL_DSL)
    compiled = GraphCompiler().compile(config)

    runtime = PregelRuntime(
        graph=compiled,
        worker=_KnowledgeWorker(),
        checkpoint_store=InMemoryCheckpointStore(),
    )

    events = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={"messages": [{"role": "user", "content": "test query"}]},
        stream_mode=StreamMode.UPDATES,
    ):
        events.append(event)

    assert len(events) >= 1
    # The worker should have produced updates with messages and context
    update_event = events[-1]
    assert update_event["type"] == "update"
    assert update_event["node"] == "retrieve"
    assert "messages" in update_event["output"]
    assert "context" in update_event["output"]


@pytest.mark.asyncio
async def test_execute_variable_set() -> None:
    """Variable-set node writes the variable to channels."""
    config = parse_graph(VARIABLE_SET_DSL)
    compiled = GraphCompiler().compile(config)

    runtime = PregelRuntime(
        graph=compiled,
        worker=_VariableWorker(),
        checkpoint_store=InMemoryCheckpointStore(),
    )

    events = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={},
        stream_mode=StreamMode.UPDATES,
    ):
        events.append(event)

    assert len(events) >= 1
    update_event = events[-1]
    assert update_event["type"] == "update"
    assert update_event["node"] == "set_var"
    assert update_event["output"].get("result") == "computed_value"


@pytest.mark.asyncio
async def test_execute_mixed_pipeline() -> None:
    """Graph with knowledge-retrieval → variable-set executes both nodes."""
    config = parse_graph(MIXED_DSL)
    compiled = GraphCompiler().compile(config)

    runtime = PregelRuntime(
        graph=compiled,
        worker=_MixedWorker(),
        checkpoint_store=InMemoryCheckpointStore(),
    )

    events = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={},
        stream_mode=StreamMode.UPDATES,
    ):
        events.append(event)

    # Should have events from both nodes
    assert len(events) >= 2
    node_ids_seen = set()
    for event in events:
        if event["type"] == "update":
            node_ids_seen.add(event["node"])
    assert "retrieve" in node_ids_seen
    assert "score_var" in node_ids_seen
