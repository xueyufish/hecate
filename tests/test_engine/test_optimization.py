"""Tests for OptimizationPass and DeadNodeElimination.

Validates the pluggable optimization contract:

- OptimizationPass ABC cannot be instantiated directly.
- DeadNodeElimination removes unreachable nodes.
"""

from __future__ import annotations

import pytest

from hecate.engine.optimization import DeadNodeElimination, OptimizationPass
from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, NodeConfig, NodeType

# --- OptimizationPass ABC ---


def test_optimization_pass_is_abstract():
    """OptimizationPass SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        OptimizationPass()  # type: ignore[abstract]


# --- Helper to create test graphs ---


def _make_graph(
    nodes: dict[str, NodeConfig],
    edges: list[Edge],
    entry_point: str = "",
) -> CompiledGraph:
    return CompiledGraph(
        nodes=nodes,
        edges=edges,
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point=entry_point,
        name="test_graph",
    )


def _node(node_id: str) -> NodeConfig:
    return NodeConfig(id=node_id, type=NodeType.CONVERSATION, config={})


# --- DeadNodeElimination ---


def test_dead_node_elimination_removes_unreachable():
    """DeadNodeElimination SHALL remove unreachable nodes."""
    graph = _make_graph(
        nodes={"A": _node("A"), "B": _node("B"), "C": _node("C")},
        edges=[Edge(source="A", target="B")],
        entry_point="A",
    )
    opt = DeadNodeElimination()
    result = opt.optimize(graph)
    assert "A" in result.nodes
    assert "B" in result.nodes
    assert "C" not in result.nodes


def test_dead_node_elimination_preserves_reachable():
    """DeadNodeElimination SHALL preserve all reachable nodes."""
    graph = _make_graph(
        nodes={"A": _node("A"), "B": _node("B"), "C": _node("C")},
        edges=[Edge(source="A", target="B"), Edge(source="B", target="C")],
        entry_point="A",
    )
    opt = DeadNodeElimination()
    result = opt.optimize(graph)
    assert len(result.nodes) == 3


def test_dead_node_elimination_no_entry_point():
    """DeadNodeElimination SHALL return graph unchanged if no entry point."""
    graph = _make_graph(
        nodes={"A": _node("A"), "B": _node("B")},
        edges=[],
        entry_point="",
    )
    opt = DeadNodeElimination()
    result = opt.optimize(graph)
    assert len(result.nodes) == 2


def test_dead_node_elimination_removes_dead_edges():
    """DeadNodeElimination SHALL remove edges to unreachable nodes."""
    graph = _make_graph(
        nodes={"A": _node("A"), "B": _node("B"), "C": _node("C")},
        edges=[Edge(source="A", target="B"), Edge(source="B", target="C")],
        entry_point="A",
    )
    opt = DeadNodeElimination()
    result = opt.optimize(graph)
    assert len(result.edges) == 2


def test_dead_node_elimination_preserves_end_sentinel():
    """DeadNodeElimination SHALL preserve edges to __end__ sentinel."""
    graph = _make_graph(
        nodes={"A": _node("A")},
        edges=[Edge(source="A", target="__end__")],
        entry_point="A",
    )
    opt = DeadNodeElimination()
    result = opt.optimize(graph)
    assert len(result.edges) == 1
