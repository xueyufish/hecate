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

import logging
from typing import Any

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
        """The template graph has 4 nodes with ``planner`` as the entry point (guard removed)."""
        config = build_three_layer_graph(
            planner_model="gpt-4o",
            sub_agent_model="gpt-4o",
        )
        assert config.name == "three-layer-agent"
        assert len(config.nodes) == 4
        assert config.entry == "planner"
        assert "guard" not in config.nodes

    def test_three_layer_compiles(self):
        """The template graph passes all compiler validation checks."""
        config = build_three_layer_graph(
            planner_model="gpt-4o",
            sub_agent_model="gpt-4o",
        )
        compiler = GraphCompiler()
        compiled = compiler.compile(config)
        assert compiled.entry_point == "planner"
        assert "guard" not in compiled.nodes


class TestHandoffEdgeParsing:
    """Validate parsing and compilation of handoff edge triggers."""

    HANDOFF_GRAPH = """
    {
        "version": "1.0",
        "name": "handoff-test",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "router": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000001"}},
            "specialist": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}}
        },
        "edges": [
            {"source": "__start__", "target": "router"},
            {"source": "router", "target": "specialist", "type": "handoff"},
            {"source": "specialist", "target": "__end__"}
        ],
        "entry": "router"
    }
    """

    def test_parse_handoff_edge_type(self):
        """Edge with type: 'handoff' is parsed with trigger='handoff'."""
        config = parse_graph(self.HANDOFF_GRAPH)
        handoff_edges = [e for e in config.edges if e.trigger == "handoff"]
        assert len(handoff_edges) == 1
        assert handoff_edges[0].source == "router"
        assert handoff_edges[0].target == "specialist"

    def test_parse_trigger_field_still_works(self):
        """Edge with trigger: 'handoff' (legacy field) is still parsed correctly."""
        dsl = {
            "version": "1.0",
            "name": "trigger-legacy",
            "state": {"messages": {"type": "topic"}},
            "nodes": {
                "a": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000001"}},
                "b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
            },
            "edges": [
                {"source": "a", "target": "b", "trigger": "handoff"},
            ],
            "entry": "a",
        }
        config = parse_graph(dsl)
        assert config.edges[0].trigger == "handoff"

    def test_handoff_edge_validates_agent_nodes(self):
        """Handoff edges between agent nodes compile successfully."""
        config = parse_graph(self.HANDOFF_GRAPH)
        compiled = GraphCompiler().compile(config)
        assert compiled is not None
        assert len(compiled.edges) == 3

    def test_standard_edge_has_no_trigger(self):
        """Standard edges (no type/trigger) have None trigger."""
        config = parse_graph(SIMPLE_LINEAR)
        for edge in config.edges:
            assert edge.trigger is None

    def test_invocation_mode_in_config(self):
        """Agent node config accepts invocation_mode field."""
        dsl = {
            "version": "1.0",
            "name": "tool-mode",
            "state": {"messages": {"type": "topic"}},
            "nodes": {
                "supervisor": {
                    "type": "agent",
                    "config": {
                        "agent_id": "00000000-0000-0000-0000-000000000001",
                        "invocation_mode": "tool",
                    },
                },
                "worker": {
                    "type": "agent",
                    "config": {"agent_id": "00000000-0000-0000-0000-000000000002"},
                },
            },
            "edges": [
                {"source": "supervisor", "target": "worker"},
                {"source": "worker", "target": "__end__"},
            ],
            "entry": "supervisor",
        }
        config = parse_graph(dsl)
        assert config.nodes["supervisor"].config.get("invocation_mode") == "tool"


class TestOptimizationPassIntegration:
    """Test that GraphCompiler applies optimization passes correctly."""

    GRAPH_WITH_UNREACHABLE = """
    {
        "version": "1.0",
        "name": "test-unreachable",
        "state": {"messages": {"type": "topic", "default": []}},
        "nodes": {
            "A": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "Entry"}},
            "B": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "Reachable"}},
            "C": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "Unreachable"}}
        },
        "edges": [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "__end__"}
        ],
        "entry": "A"
    }
    """

    def test_compiler_default_no_optimization(self):
        """GraphCompiler with no passes preserves all nodes including unreachable."""
        from hecate.engine.compiler import GraphCompiler

        config = parse_graph(self.GRAPH_WITH_UNREACHABLE)
        compiled = GraphCompiler().compile(config)
        assert "A" in compiled.nodes
        assert "B" in compiled.nodes
        assert "C" in compiled.nodes

    def test_compiler_single_pass(self):
        """GraphCompiler with DeadNodeElimination removes unreachable nodes."""
        from hecate.engine.compiler import GraphCompiler
        from hecate.engine.optimization import DeadNodeElimination

        config = parse_graph(self.GRAPH_WITH_UNREACHABLE)
        compiled = GraphCompiler(passes=[DeadNodeElimination()]).compile(config)
        assert "A" in compiled.nodes
        assert "B" in compiled.nodes
        assert "C" not in compiled.nodes

    def test_compiler_multi_pass_pipeline(self):
        """GraphCompiler applies passes in order."""
        from hecate.engine.compiler import GraphCompiler
        from hecate.engine.optimization import DeadNodeElimination

        config = parse_graph(self.GRAPH_WITH_UNREACHABLE)
        compiler = GraphCompiler(passes=[DeadNodeElimination(), DeadNodeElimination()])
        compiled = compiler.compile(config)
        assert "A" in compiled.nodes
        assert "B" in compiled.nodes
        assert "C" not in compiled.nodes


class TestPersistentField:
    """Tests for persistent field parsing and deprecated persistent_topic migration."""

    def test_persistent_field_parsed(self) -> None:
        """Parse persistent=true from JSON."""
        graph = parse_graph(
            {
                "version": "1.0",
                "name": "persistent-test",
                "state": {
                    "audit_log": {"type": "topic", "persistent": True, "default": []},
                    "messages": {"type": "topic", "default": []},
                },
                "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "test"}}},
                "edges": [{"source": "A", "target": "__end__"}],
                "entry": "A",
            }
        )
        assert graph.state["audit_log"].persistent is True
        assert graph.state["messages"].persistent is False

    def test_persistent_defaults_false(self) -> None:
        """Persistent defaults to false when not specified."""
        graph = parse_graph(
            {
                "version": "1.0",
                "name": "default-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "test"}}},
                "edges": [{"source": "A", "target": "__end__"}],
                "entry": "A",
            }
        )
        assert graph.state["messages"].persistent is False

    def test_deprecated_persistent_topic_migrated(self) -> None:
        """Auto-migrate persistent_topic to topic + persistent=True."""
        graph = parse_graph(
            {
                "version": "1.0",
                "name": "migration-test",
                "state": {"audit_log": {"type": "persistent_topic", "default": []}},
                "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "test"}}},
                "edges": [{"source": "A", "target": "__end__"}],
                "entry": "A",
            }
        )

        assert graph.state["audit_log"].type.value == "topic"
        assert graph.state["audit_log"].persistent is True

    def test_compiled_graph_to_json_includes_persistent(self) -> None:
        """CompiledGraph.to_json() includes persistent field."""
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "serialize-test",
                "state": {
                    "audit_log": {"type": "topic", "persistent": True, "default": []},
                    "messages": {"type": "topic", "default": []},
                },
                "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o", "system_prompt": "test"}}},
                "edges": [{"source": "A", "target": "__end__"}],
                "entry": "A",
            }
        )
        compiled = GraphCompiler().compile(graph)
        json_data = compiled.to_json()
        assert json_data["state"]["audit_log"]["persistent"] is True
        assert json_data["state"]["messages"]["persistent"] is False


class TestRoutingModeParsing:
    """Tests for parsing routing_mode and routing_config from graph DSL."""

    def test_parse_intent_routing_config(self) -> None:
        graph = parse_graph(
            {
                "version": "1.0",
                "name": "intent-routing-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {
                            "expression": "category",
                            "routing_mode": "intent",
                            "routing_config": {
                                "intent_patterns": [{"pattern": "billing", "target": "billing_agent"}],
                                "routing_prompt": "Classify intent",
                            },
                        },
                    },
                    "billing_agent": {"type": "agent", "config": {"agent_id": "uuid-1"}},
                },
                "edges": [
                    {"source": "start", "target": "router"},
                    {"source": "router", "target": {"billing_agent": "billing_agent", "default": "start"}},
                ],
                "entry": "start",
            }
        )
        router_node = graph.nodes["router"]
        assert router_node.config["routing_mode"] == "intent"
        assert len(router_node.config["routing_config"]["intent_patterns"]) == 1

    def test_parse_dynamic_routing_config(self) -> None:
        graph = parse_graph(
            {
                "version": "1.0",
                "name": "dynamic-routing-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {
                            "routing_mode": "dynamic",
                            "routing_config": {
                                "candidate_agents": ["agent_a", "agent_b"],
                                "routing_prompt": "Select best agent",
                                "allow_repeated_speaker": True,
                            },
                        },
                    },
                    "agent_a": {"type": "agent", "config": {"agent_id": "uuid-a"}},
                    "agent_b": {"type": "agent", "config": {"agent_id": "uuid-b"}},
                },
                "edges": [
                    {"source": "start", "target": "router"},
                    {"source": "router", "target": {"agent_a": "agent_a", "agent_b": "agent_b"}},
                ],
                "entry": "start",
            }
        )
        router_node = graph.nodes["router"]
        assert router_node.config["routing_mode"] == "dynamic"
        assert router_node.config["routing_config"]["candidate_agents"] == ["agent_a", "agent_b"]
        assert router_node.config["routing_config"]["allow_repeated_speaker"] is True

    def test_invalid_routing_mode_raises(self) -> None:
        with pytest.raises(GraphValidationError, match="unknown"):
            parse_graph(
                {
                    "version": "1.0",
                    "name": "bad-routing-test",
                    "state": {"messages": {"type": "topic", "default": []}},
                    "nodes": {
                        "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                        "router": {"type": "condition", "config": {"routing_mode": "unknown"}},
                    },
                    "edges": [{"source": "start", "target": "router"}],
                    "entry": "start",
                }
            )

    def test_parse_dynamic_handoff_trigger(self) -> None:
        graph = parse_graph(
            {
                "version": "1.0",
                "name": "dynamic-handoff-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                },
                "edges": [
                    {"source": "start", "target": "__end__", "trigger": "dynamic_handoff"},
                ],
                "entry": "start",
            }
        )
        dyn_edges = [e for e in graph.edges if e.trigger == "dynamic_handoff"]
        assert len(dyn_edges) == 1


class TestCompilerChannelAccessAndRouting:
    """Tests for compiler channel access validation and routing config validation."""

    def test_compiler_warns_on_nonexistent_readable_channel(self, caplog: Any) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "channel-access-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {
                        "type": "conversation",
                        "config": {
                            "model": "gpt-4o",
                            "channels": {"readable": ["nonexistent"], "writable": []},
                        },
                    },
                },
                "edges": [{"source": "start", "target": "__end__"}],
                "entry": "start",
            }
        )
        with caplog.at_level(logging.WARNING):
            compiled = GraphCompiler().compile(graph)
        assert compiled is not None
        assert any("nonexistent" in r.message and "readable" in r.message for r in caplog.records)

    def test_compiler_warns_on_nonexistent_writable_channel(self, caplog: Any) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "channel-access-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {
                        "type": "conversation",
                        "config": {
                            "model": "gpt-4o",
                            "channels": {"readable": [], "writable": ["nonexistent"]},
                        },
                    },
                },
                "edges": [{"source": "start", "target": "__end__"}],
                "entry": "start",
            }
        )
        with caplog.at_level(logging.WARNING):
            compiled = GraphCompiler().compile(graph)
        assert compiled is not None
        assert any("nonexistent" in r.message and "writable" in r.message for r in caplog.records)

    def test_compiler_rejects_intent_mode_without_patterns(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "routing-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {"routing_mode": "intent", "routing_config": {}},
                    },
                },
                "edges": [{"source": "start", "target": "router"}],
                "entry": "start",
            }
        )
        with pytest.raises(GraphValidationError, match="intent_patterns"):
            GraphCompiler().compile(graph)

    def test_compiler_rejects_dynamic_mode_without_candidates(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "routing-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {"routing_mode": "dynamic", "routing_config": {}},
                    },
                },
                "edges": [{"source": "start", "target": "router"}],
                "entry": "start",
            }
        )
        with pytest.raises(GraphValidationError, match="candidate_agents"):
            GraphCompiler().compile(graph)

    def test_compiler_rejects_dynamic_mode_with_nonexistent_candidate(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "routing-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {
                            "routing_mode": "dynamic",
                            "routing_config": {"candidate_agents": ["nonexistent"]},
                        },
                    },
                },
                "edges": [{"source": "start", "target": "router"}],
                "entry": "start",
            }
        )
        with pytest.raises(GraphValidationError, match="not a declared node"):
            GraphCompiler().compile(graph)

    def test_compiler_populates_channel_access_map(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "access-map-test",
                "state": {
                    "messages": {"type": "topic", "default": []},
                    "context": {"type": "last_value"},
                },
                "nodes": {
                    "start": {
                        "type": "conversation",
                        "config": {
                            "model": "gpt-4o",
                            "channels": {
                                "readable": ["messages", "context"],
                                "writable": ["messages"],
                            },
                        },
                    },
                },
                "edges": [{"source": "start", "target": "__end__"}],
                "entry": "start",
            }
        )
        compiled = GraphCompiler().compile(graph)
        assert "start" in compiled.channel_access
        assert compiled.channel_access["start"].readable == {"messages", "context"}
        assert compiled.channel_access["start"].writable == {"messages"}

    def test_compiler_accepts_valid_intent_routing(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "valid-intent-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {
                            "routing_mode": "intent",
                            "routing_config": {
                                "intent_patterns": [{"pattern": "billing", "target": "billing"}],
                            },
                        },
                    },
                    "billing": {"type": "agent", "config": {"agent_id": "uuid-b"}},
                },
                "edges": [
                    {"source": "start", "target": "router"},
                    {"source": "router", "target": {"billing": "billing", "default": "start"}},
                ],
                "entry": "start",
            }
        )
        compiled = GraphCompiler().compile(graph)
        assert compiled is not None

    def test_compiler_accepts_valid_dynamic_routing(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "valid-dynamic-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
                    "router": {
                        "type": "condition",
                        "config": {
                            "routing_mode": "dynamic",
                            "routing_config": {
                                "candidate_agents": ["agent_a"],
                                "routing_prompt": "Select agent",
                            },
                        },
                    },
                    "agent_a": {"type": "agent", "config": {"agent_id": "uuid-a"}},
                },
                "edges": [
                    {"source": "start", "target": "router"},
                    {"source": "router", "target": {"agent_a": "agent_a"}},
                ],
                "entry": "start",
            }
        )
        compiled = GraphCompiler().compile(graph)
        assert compiled is not None


class TestDynamicHandoff:
    """Tests for dynamic_handoff edge trigger support."""

    def test_dynamic_handoff_cycle_detection(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "cycle-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "a": {"type": "agent", "config": {"agent_id": "uuid-a"}},
                    "b": {"type": "agent", "config": {"agent_id": "uuid-b"}},
                    "c": {"type": "agent", "config": {"agent_id": "uuid-c"}},
                },
                "edges": [
                    {"source": "a", "target": "b", "trigger": "dynamic_handoff"},
                    {"source": "b", "target": "c", "trigger": "dynamic_handoff"},
                    {"source": "c", "target": "a", "trigger": "dynamic_handoff"},
                ],
                "entry": "a",
            }
        )
        with pytest.raises(GraphValidationError, match="[Cc]ircular"):
            GraphCompiler().compile(graph)

    def test_dynamic_handoff_validates_agent_nodes(self) -> None:
        from hecate.engine.compiler import GraphCompiler

        graph = parse_graph(
            {
                "version": "1.0",
                "name": "non-agent-test",
                "state": {"messages": {"type": "topic", "default": []}},
                "nodes": {
                    "a": {"type": "agent", "config": {"agent_id": "uuid-a"}},
                    "conv": {"type": "conversation", "config": {"model": "gpt-4o"}},
                },
                "edges": [
                    {"source": "a", "target": "conv", "trigger": "dynamic_handoff"},
                ],
                "entry": "a",
            }
        )
        with pytest.raises(GraphValidationError, match="agent node"):
            GraphCompiler().compile(graph)
