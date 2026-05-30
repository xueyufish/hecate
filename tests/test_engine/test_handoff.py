"""Tests for handoff tool generation and cycle detection."""

from __future__ import annotations

import pytest

from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import GraphValidationError, parse_graph
from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, NodeConfig, NodeType
from hecate.services.orchestration.handoff import (
    build_handoff_tool_schema,
    create_handoff_worker_result,
    get_handoff_targets_for_node,
    inject_handoff_tools,
    is_handoff_tool_call,
)


def _make_compiled_with_handoff(
    edges: list[tuple[str, str, str | None]],
) -> CompiledGraph:
    """Create a minimal compiled graph with agent nodes and handoff edges."""
    node_ids: set[str] = set()
    for src, tgt, _ in edges:
        node_ids.add(src)
        node_ids.add(tgt)

    nodes = {nid: NodeConfig(id=nid, type=NodeType.AGENT) for nid in node_ids}
    edge_objects = [Edge(source=src, target=tgt, trigger=trigger) for src, tgt, trigger in edges]
    return CompiledGraph(
        nodes=nodes,
        edges=edge_objects,
        channels={"messages": ChannelDef(type=ChannelType.TOPIC)},
        entry_point=next(iter(node_ids)) if node_ids else "",
    )


def test_build_handoff_tool_schema():
    """Handoff tool schema has correct structure and valid targets."""
    schema = build_handoff_tool_schema(["billing", "technical"])

    assert schema["type"] == "function"
    func = schema["function"]
    assert func["name"] == "handoff_to_agent"
    params = func["parameters"]["properties"]["target"]
    assert params["enum"] == ["billing", "technical"]
    assert "target" in func["parameters"]["required"]


def test_get_handoff_targets_for_node():
    """Returns only handoff edge targets for a specific node."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "handoff"),
            ("router", "technical", "handoff"),
            ("billing", "__end__", None),
        ]
    )

    targets = get_handoff_targets_for_node(compiled, "router")

    assert set(targets) == {"billing", "technical"}


def test_get_handoff_targets_no_handoff_edges():
    """Returns empty list when node has no handoff edges."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", None),
        ]
    )

    targets = get_handoff_targets_for_node(compiled, "router")

    assert targets == []


def test_inject_handoff_tools_adds_tool():
    """Injects handoff tool when handoff edges exist for the node."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "handoff"),
        ]
    )
    existing_tools = [{"type": "function", "function": {"name": "search"}}]

    result = inject_handoff_tools(existing_tools, compiled, "router")

    assert len(result) == 2
    assert result[0]["function"]["name"] == "search"
    assert result[1]["function"]["name"] == "handoff_to_agent"


def test_inject_handoff_tools_no_change():
    """Returns tools unchanged when no handoff edges exist."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", None),
        ]
    )
    existing_tools = [{"type": "function", "function": {"name": "search"}}]

    result = inject_handoff_tools(existing_tools, compiled, "router")

    assert len(result) == 1
    assert result[0]["function"]["name"] == "search"


def test_is_handoff_tool_call():
    """Correctly identifies handoff tool calls."""
    assert is_handoff_tool_call("handoff_to_agent") is True
    assert is_handoff_tool_call("search") is False
    assert is_handoff_tool_call("handoff") is False


def test_create_handoff_worker_result():
    """Handoff worker result contains Command(goto=target)."""
    result = create_handoff_worker_result("router", "billing")

    assert result.node_id == "router"
    assert result.command is not None
    assert result.command.goto == "billing"
    assert any("Transferring" in m.get("content", "") for m in result.channel_updates["messages"])


def test_handoff_cycle_detection_raises():
    """Compiler rejects circular handoff chains."""
    dsl = {
        "version": "1.0",
        "name": "circular-handoff",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "a": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000001"}},
            "b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
            "c": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000003"}},
        },
        "edges": [
            {"source": "a", "target": "b", "type": "handoff"},
            {"source": "b", "target": "c", "type": "handoff"},
            {"source": "c", "target": "a", "type": "handoff"},
        ],
        "entry": "a",
    }

    graph_config = parse_graph(dsl)
    with pytest.raises(GraphValidationError, match="Circular handoff"):
        GraphCompiler().compile(graph_config)


def test_handoff_non_circular_accepted():
    """Compiler accepts non-circular handoff chains."""
    dsl = {
        "version": "1.0",
        "name": "linear-handoff",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "a": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000001"}},
            "b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
        },
        "edges": [
            {"source": "a", "target": "b", "type": "handoff"},
            {"source": "b", "target": "__end__"},
        ],
        "entry": "a",
    }

    graph_config = parse_graph(dsl)
    compiled = GraphCompiler().compile(graph_config)
    assert compiled is not None


def test_handoff_non_agent_source_rejected():
    """Compiler rejects handoff from non-agent node."""
    dsl = {
        "version": "1.0",
        "name": "bad-handoff-source",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "conv": {"type": "conversation", "config": {"model": "gpt-4o"}},
            "agent_b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
        },
        "edges": [
            {"source": "conv", "target": "agent_b", "type": "handoff"},
            {"source": "agent_b", "target": "__end__"},
        ],
        "entry": "conv",
    }

    graph_config = parse_graph(dsl)
    with pytest.raises(GraphValidationError, match="must be an agent node"):
        GraphCompiler().compile(graph_config)
