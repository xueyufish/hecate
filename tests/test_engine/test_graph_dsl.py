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
    def test_parse_valid_linear_graph(self):
        config = parse_graph(SIMPLE_LINEAR)
        assert config.name == "test-linear"
        assert len(config.nodes) == 2
        assert config.entry == "A"

    def test_parse_valid_conditional_graph(self):
        config = parse_graph(CONDITIONAL_GRAPH)
        assert len(config.nodes) == 3
        assert len(config.edges) == 3

    def test_parse_dict_input(self):
        import json
        data = json.loads(SIMPLE_LINEAR)
        config = parse_graph(data)
        assert config.name == "test-linear"

    def test_invalid_json_raises(self):
        with pytest.raises(GraphValidationError, match="Invalid JSON"):
            parse_graph("{bad json")

    def test_missing_required_field_raises(self):
        with pytest.raises(GraphValidationError):
            parse_graph('{"version": "1.0"}')

    def test_unknown_node_type_raises(self):
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
    def setup_method(self):
        self.compiler = GraphCompiler()

    def test_compile_linear_graph(self):
        config = parse_graph(SIMPLE_LINEAR)
        compiled = self.compiler.compile(config)
        assert compiled.entry_point == "A"
        assert len(compiled.edges) == 2

    def test_compile_conditional_graph(self):
        config = parse_graph(CONDITIONAL_GRAPH)
        compiled = self.compiler.compile(config)
        assert compiled.entry_point == "start"
        assert len(compiled.nodes) == 3

    def test_dangling_edge_target_raises(self):
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
        from hecate.engine.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType
        config = GraphConfig(
            name="orphan",
            state={"messages": ChannelDef(type=ChannelType.TOPIC)},
            nodes={
                "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
                "orphan": NodeConfig(id="orphan", type=NodeType.CONVERSATION, config={"model": "x", "system_prompt": "x"}),
            },
            edges=[Edge(source="A", target="__end__")],
            entry="A",
        )
        warnings = self.compiler._detect_unreachable(config)
        assert "orphan" in warnings

    def test_compiled_graph_to_json_roundtrip(self):
        config = parse_graph(SIMPLE_LINEAR)
        compiled = self.compiler.compile(config)
        json_data = compiled.to_json()
        assert json_data["entry"] == "A"
        assert json_data["version"] == "1.0"


class TestThreeLayerTemplate:
    def test_build_three_layer_graph(self):
        config = build_three_layer_graph(
            guard_model="gpt-4o",
            planner_model="gpt-4o",
            sub_agent_model="gpt-4o",
        )
        assert config.name == "three-layer-agent"
        assert len(config.nodes) == 5
        assert config.entry == "guard"

    def test_three_layer_compiles(self):
        config = build_three_layer_graph(
            guard_model="gpt-4o",
            planner_model="gpt-4o",
            sub_agent_model="gpt-4o",
        )
        compiler = GraphCompiler()
        compiled = compiler.compile(config)
        assert compiled.entry_point == "guard"

