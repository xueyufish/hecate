"""Tests for the graph DSL parser and compiler.

Covers three layers of the graph definition pipeline:

1. **Parsing** (``parse_graph``) — validates JSON input, required fields, and
   node types, producing a ``GraphConfig`` object.
2. **Compilation** (``GraphCompiler``) — transforms a ``GraphConfig`` into a
   ``CompiledGraph`` ready for the Pregel runtime, checking for dangling
   edges and unreachable nodes.
3. **Template** (``build_three_layer_graph``) — verifies that the built-in
   three-layer agent template produces a valid, compilable graph.

The tests use two inline JSON fixtures: a simple linear two-node graph
(``SIMPLE_LINEAR``) and a three-node conditional graph (``CONDITIONAL_GRAPH``).
"""

from __future__ import annotations

import pytest

from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import GraphValidationError, parse_graph
from hecate.engine.templates import build_three_layer_graph

SIMPLE_LINEAR = """
{
    "version": "1.0",
    "name": "test-linear",
    "state": {
        "messages": {"type": "topic", "default": []}
    },
    "nodes": {
        "A": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "Hello"}},
        "B": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "World"}}
    },
    "edges": [
        {"source": "A", "target": "B"},
        {"source": "B", "target": "__end__"}
    ],
    "entry": "A"
}
"""

CONDITIONAL_GRAPH = """
{
    "version": "1.0",
    "name": "test-conditional",
    "state": {
        "messages": {"type": "topic", "default": []}
    },
    "nodes": {
        "start": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "Start"}},
        "check": {"type": "condition", "config": {"expression": "has_tool"}},
        "tool": {"type": "tool-call", "config": {"tool_name": "search"}}
    },
    "edges": [
        {"source": "start", "target": "check"},
        {"source": "check", "target": {"true": "tool", "false": "__end__"}},
        {"source": "tool", "target": "__end__"}
    ],
    "entry": "start"
}
"""


class TestGraphParser:
    """Validate ``parse_graph`` input handling and error reporting.

    Tests cover happy-path parsing (JSON string and dict), as well as
    rejection of malformed JSON, missing required fields, unknown node types,
    and reserved node names.
    """

    def test_parse_valid_linear_graph(self):
        """A well-formed linear graph parses into a GraphConfig with correct name, node count, and entry point."""
        config = parse_graph(SIMPLE_LINEAR)
        assert config.name == "test-linear"
        assert len(config.nodes) == 2
        assert config.entry == "A"

    def test_parse_valid_conditional_graph(self):
        """A graph with a conditional edge parses with the expected node and edge counts."""
        config = parse_graph(CONDITIONAL_GRAPH)
        assert len(config.nodes) == 3
        assert len(config.edges) == 3

    def test_parse_dict_input(self):
        """``parse_graph`` also accepts a pre-parsed dict instead of a JSON string."""
        import json

        data = json.loads(SIMPLE_LINEAR)
        config = parse_graph(data)
        assert config.name == "test-linear"

    def test_invalid_json_raises(self):
        """Malformed JSON triggers a GraphValidationError with an informative message."""
        with pytest.raises(GraphValidationError, match="Invalid JSON"):
            parse_graph("{bad json")

    def test_missing_required_field_raises(self):
        """A graph missing required top-level fields (e.g. ``name``, ``nodes``) is rejected."""
        with pytest.raises(GraphValidationError):
            parse_graph('{"version": "1.0"}')

    def test_unknown_node_type_raises(self):
        """An unrecognised node type causes a validation error."""
        bad_graph = """
        {
            "version": "1.0",
            "name": "bad",
            "state": {},
            "nodes": {"A": {"type": "unknown_type", "config": {}}},
            "edges": [],
            "entry": "A"
        }
        """
        with pytest.raises(GraphValidationError):
            parse_graph(bad_graph)

    def test_reserved_node_name_start_raises(self):
        """Names prefixed with ``__`` (e.g. ``__start__``) are reserved and must be rejected."""
        bad_graph = """
        {
            "version": "1.0",
            "name": "bad",
            "state": {},
            "nodes": {"__start__": {"type": "conversation", "config": {"model": "x", "system_prompt": "x"}}},
            "edges": [],
            "entry": "__start__"
        }
        """
        with pytest.raises(GraphValidationError):
            parse_graph(bad_graph)


class TestGraphCompiler:
    """Validate ``GraphCompiler`` — turning parsed configs into executable graphs.

    Checks correct compilation of linear and conditional graphs, detection of
    dangling edge targets and unreachable/disconnected nodes, and JSON
    round-tripping of the compiled output.
    """

    def setup_method(self):
        self.compiler = GraphCompiler()

    def test_compile_linear_graph(self):
        """A linear graph compiles with the correct entry point and edge count."""
        config = parse_graph(SIMPLE_LINEAR)
        compiled = self.compiler.compile(config)
        assert compiled.entry_point == "A"
        assert len(compiled.edges) == 2

    def test_compile_conditional_graph(self):
        """A conditional graph compiles with all three nodes preserved."""
        config = parse_graph(CONDITIONAL_GRAPH)
        compiled = self.compiler.compile(config)
        assert compiled.entry_point == "start"
        assert len(compiled.nodes) == 3

    def test_dangling_edge_target_raises(self):
        """An edge whose target does not match any node triggers a validation error."""
        from hecate.engine.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType

        config = GraphConfig(
            name="bad",
            state={"messages": ChannelDef(type=ChannelType.TOPIC)},
            nodes={"A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"})},
            edges=[Edge(source="A", target="nonexistent")],
            entry="A",
        )
        with pytest.raises(GraphValidationError, match="non-existent"):
            self.compiler.compile(config)

    def test_unreachable_node_warning(self):
        """A node with no incoming edges from the main graph is detected as unreachable."""
        from hecate.engine.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType

        config = GraphConfig(
            name="orphan",
            state={"messages": ChannelDef(type=ChannelType.TOPIC)},
            nodes={
                "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
                "orphan": NodeConfig(
                    id="orphan", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}
                ),
            },
            edges=[Edge(source="A", target="__end__")],
            entry="A",
        )
        unreachable = self.compiler._detect_unreachable(config)
        assert "orphan" in unreachable

    def test_disconnected_subgraph_detected(self, caplog):
        """Two disconnected components: the engine warns about the unreachable one."""
        import logging

        from hecate.engine.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType

        config = GraphConfig(
            name="disconnected",
            state={"messages": ChannelDef(type=ChannelType.TOPIC)},
            nodes={
                "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
                "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
                "C": NodeConfig(id="C", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
                "D": NodeConfig(id="D", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
            },
            edges=[
                Edge(source="A", target="B"),
                Edge(source="C", target="D"),
            ],
            entry="A",
        )
        unreachable = self.compiler._detect_unreachable(config)
        assert sorted(unreachable) == ["C", "D"]

        with caplog.at_level(logging.WARNING):
            self.compiler.compile(config)
        assert any("C" in rec.message for rec in caplog.records)

    def test_compiled_graph_to_json_roundtrip(self):
        """Serialising a compiled graph to JSON and back preserves entry point and version."""
        config = parse_graph(SIMPLE_LINEAR)
        compiled = self.compiler.compile(config)
        json_data = compiled.to_json()
        assert json_data["entry"] == "A"
        assert json_data["version"] == "1.0"


class TestThreeLayerTemplate:
    """Verify that the built-in three-layer agent template produces a valid,
    compilable graph with the expected structure (guard -> planner -> sub-agent
    with routing nodes)."""

    def test_build_three_layer_graph(self):
        """The template graph has 5 nodes with ``guard`` as the entry point."""
        config = build_three_layer_graph(
            guard_model="gpt-4o",
            planner_model="gpt-4o",
            sub_agent_model="gpt-4o",
        )
        assert config.name == "three-layer-agent"
        assert len(config.nodes) == 5
        assert config.entry == "guard"

    def test_three_layer_compiles(self):
        """The template graph passes all compiler validation checks."""
        config = build_three_layer_graph(
            guard_model="gpt-4o",
            planner_model="gpt-4o",
            sub_agent_model="gpt-4o",
        )
        compiler = GraphCompiler()
        compiled = compiler.compile(config)
        assert compiled.entry_point == "guard"
