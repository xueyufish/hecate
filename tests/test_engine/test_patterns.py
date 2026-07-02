"""Tests for engine/patterns.py — collaboration pattern classification and generation."""

from __future__ import annotations

import pytest

from hecate.engine.patterns import (
    PATTERN_DEFINITIONS,
    CollaborationPattern,
    build_graph_from_pattern,
    infer_pattern,
)
from hecate.engine.templates import (
    build_broadcast_pipeline,
    build_debate_graph,
    build_fan_out_pipeline,
    build_negotiation_graph,
    build_sequential_pipeline,
)
from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
)

# ---------------------------------------------------------------------------
# CollaborationPattern enum
# ---------------------------------------------------------------------------


class TestCollaborationPattern:
    """Tests for the CollaborationPattern enum."""

    def test_has_exactly_six_members(self) -> None:
        assert len(CollaborationPattern) == 6

    def test_members_are_expected_values(self) -> None:
        expected = {"sequential", "parallel", "handoff", "broadcast", "negotiation", "debate"}
        actual = {m.value for m in CollaborationPattern}
        assert actual == expected

    def test_values_are_lowercase_strings(self) -> None:
        for member in CollaborationPattern:
            assert member.value == member.value.lower()
            assert isinstance(member.value, str)

    def test_enum_access_by_name(self) -> None:
        assert CollaborationPattern.SEQUENTIAL == "sequential"
        assert CollaborationPattern.PARALLEL == "parallel"
        assert CollaborationPattern.HANDOFF == "handoff"
        assert CollaborationPattern.BROADCAST == "broadcast"
        assert CollaborationPattern.NEGOTIATION == "negotiation"
        assert CollaborationPattern.DEBATE == "debate"


# ---------------------------------------------------------------------------
# infer_pattern
# ---------------------------------------------------------------------------


class TestInferPattern:
    """Tests for infer_pattern() structural heuristic."""

    def test_sequential_pipeline_detected(self) -> None:
        config = build_sequential_pipeline(
            stages=[
                {"id": "s1", "model": "gpt-4o", "system_prompt": "Step 1"},
                {"id": "s2", "model": "gpt-4o", "system_prompt": "Step 2"},
            ],
        )
        result = infer_pattern(config)
        assert result == CollaborationPattern.SEQUENTIAL

    def test_parallel_fan_out_detected(self) -> None:
        config = build_fan_out_pipeline(
            researcher_model="gpt-4o",
            analyst_model="gpt-4o",
            summarizer_model="gpt-4o",
        )
        result = infer_pattern(config)
        assert result == CollaborationPattern.PARALLEL

    def test_handoff_pattern_detected(self) -> None:
        config = _make_handoff_graph()
        result = infer_pattern(config)
        assert result == CollaborationPattern.HANDOFF

    def test_broadcast_pattern_detected(self) -> None:
        config = build_broadcast_pipeline(
            participants=[
                {"id": "p1", "model": "gpt-4o", "system_prompt": "P1"},
                {"id": "p2", "model": "gpt-4o", "system_prompt": "P2"},
            ],
        )
        result = infer_pattern(config)
        assert result == CollaborationPattern.BROADCAST

    def test_negotiation_pattern_detected(self) -> None:
        config = build_negotiation_graph(
            proposer_model="gpt-4o",
            responder_model="gpt-4o",
        )
        result = infer_pattern(config)
        assert result == CollaborationPattern.NEGOTIATION

    def test_debate_pattern_detected(self) -> None:
        config = build_debate_graph(
            debater_a_model="gpt-4o",
            debater_b_model="gpt-4o",
            judge_model="gpt-4o",
            rounds=3,
        )
        result = infer_pattern(config)
        assert result == CollaborationPattern.DEBATE

    def test_unknown_pattern_returns_none(self) -> None:
        # A graph with a single agent node, no edges — no pattern
        config = GraphConfig(
            name="empty",
            nodes={
                "only": NodeConfig(id="only", type=NodeType.AGENT, config={}),
            },
            edges=[],
            state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry="only",
        )
        assert infer_pattern(config) is None

    def test_empty_graph_returns_none(self) -> None:
        config = GraphConfig(name="empty")
        assert infer_pattern(config) is None


# ---------------------------------------------------------------------------
# build_graph_from_pattern
# ---------------------------------------------------------------------------


class TestBuildGraphFromPattern:
    """Tests for build_graph_from_pattern() generation."""

    def test_sequential_generates_valid_graph(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.SEQUENTIAL,
            {
                "stages": [
                    {"id": "s1", "model": "gpt-4o", "system_prompt": "Step 1"},
                    {"id": "s2", "model": "gpt-4o", "system_prompt": "Step 2"},
                ],
            },
        )
        assert isinstance(graph, GraphConfig)
        assert len(graph.nodes) == 2
        assert graph.entry == "s1"

    def test_parallel_generates_fan_out_merge(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.PARALLEL,
            {
                "coordinator": {"model": "gpt-4o", "system_prompt": "Coord"},
                "workers": [
                    {"model": "gpt-4o", "system_prompt": "W1"},
                    {"model": "gpt-4o", "system_prompt": "W2"},
                ],
                "aggregator": {"model": "gpt-4o", "system_prompt": "Agg"},
            },
        )
        assert isinstance(graph, GraphConfig)
        node_types = {n.type for n in graph.nodes.values()}
        assert NodeType.FAN_OUT in node_types
        assert NodeType.MERGE in node_types

    def test_handoff_generates_handoff_edges(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.HANDOFF,
            {
                "router": {"id": "router", "model": "gpt-4o", "system_prompt": "Route"},
                "specialists": [
                    {"id": "spec1", "model": "gpt-4o", "system_prompt": "Spec 1"},
                    {"id": "spec2", "model": "gpt-4o", "system_prompt": "Spec 2"},
                ],
            },
        )
        assert isinstance(graph, GraphConfig)
        handoff_edges = [e for e in graph.edges if e.trigger == "handoff"]
        assert len(handoff_edges) == 2

    def test_broadcast_generates_shared_topic(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.BROADCAST,
            {
                "participants": [
                    {"id": "p1", "model": "gpt-4o", "system_prompt": "P1"},
                    {"id": "p2", "model": "gpt-4o", "system_prompt": "P2"},
                ],
            },
        )
        assert isinstance(graph, GraphConfig)
        assert "messages" in graph.state
        assert graph.state["messages"].type == ChannelType.TOPIC

    def test_negotiation_generates_loop(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.NEGOTIATION,
            {
                "proposer": {"model": "gpt-4o", "system_prompt": "Propose"},
                "responder": {"model": "gpt-4o", "system_prompt": "Respond"},
            },
        )
        assert isinstance(graph, GraphConfig)
        # Condition edge should have dict target with loop
        condition_edges = [e for e in graph.edges if isinstance(e.target, dict)]
        assert len(condition_edges) >= 1
        assert "agreement_status" in graph.state

    def test_debate_generates_round_counter(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.DEBATE,
            {
                "debater_a": {"model": "gpt-4o", "system_prompt": "A"},
                "debater_b": {"model": "gpt-4o", "system_prompt": "B"},
                "rounds": 3,
            },
        )
        assert isinstance(graph, GraphConfig)
        assert "debate_round" in graph.state

    def test_debate_with_judge(self) -> None:
        graph = build_graph_from_pattern(
            CollaborationPattern.DEBATE,
            {
                "debater_a": {"model": "gpt-4o", "system_prompt": "A"},
                "debater_b": {"model": "gpt-4o", "system_prompt": "B"},
                "judge": {"model": "gpt-4o", "system_prompt": "Judge"},
                "rounds": 3,
            },
        )
        assert "judge" in graph.nodes

    def test_invalid_pattern_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            build_graph_from_pattern("invalid", {})  # type: ignore[arg-type]

    def test_sequential_missing_stages_raises(self) -> None:
        with pytest.raises(ValueError, match="stages"):
            build_graph_from_pattern(CollaborationPattern.SEQUENTIAL, {})

    def test_handoff_missing_params_raises(self) -> None:
        with pytest.raises(ValueError, match="router"):
            build_graph_from_pattern(CollaborationPattern.HANDOFF, {})


# ---------------------------------------------------------------------------
# PATTERN_DEFINITIONS
# ---------------------------------------------------------------------------


class TestPatternDefinitions:
    """Tests for PATTERN_DEFINITIONS metadata."""

    def test_has_six_definitions(self) -> None:
        assert len(PATTERN_DEFINITIONS) == 6

    def test_each_has_required_fields(self) -> None:
        for pdef in PATTERN_DEFINITIONS:
            assert "id" in pdef
            assert "name" in pdef
            assert "description" in pdef
            assert "parameters" in pdef
            assert "preview" in pdef
            assert isinstance(pdef["parameters"], dict)

    def test_parameter_schemas_have_type_object(self) -> None:
        for pdef in PATTERN_DEFINITIONS:
            assert pdef["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handoff_graph() -> GraphConfig:
    """Build a simple handoff graph for testing."""
    return GraphConfig(
        name="handoff-test",
        nodes={
            "router": NodeConfig(
                id="router",
                type=NodeType.AGENT,
                config={
                    "model": "gpt-4o",
                    "system_prompt": "Router",
                    "channels": {"readable": ["messages"], "writable": ["messages"]},
                },
            ),
            "spec_a": NodeConfig(
                id="spec_a",
                type=NodeType.AGENT,
                config={
                    "model": "gpt-4o",
                    "system_prompt": "Spec A",
                    "channels": {"readable": ["messages"], "writable": ["messages"]},
                },
            ),
            "spec_b": NodeConfig(
                id="spec_b",
                type=NodeType.AGENT,
                config={
                    "model": "gpt-4o",
                    "system_prompt": "Spec B",
                    "channels": {"readable": ["messages"], "writable": ["messages"]},
                },
            ),
        },
        edges=[
            Edge(source="router", target="spec_a", trigger="handoff"),
            Edge(source="router", target="spec_b", trigger="handoff"),
            Edge(source="spec_a", target="__end__", trigger="handoff"),
            Edge(source="spec_b", target="__end__", trigger="handoff"),
        ],
        state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry="router",
    )
