"""Tests for negotiation and debate graph templates."""

from __future__ import annotations

from hecate.engine.compiler import GraphCompiler
from hecate.engine.templates import build_debate_graph, build_negotiation_graph
from hecate.engine.types import NodeType


class TestNegotiationGraph:
    """Tests for build_negotiation_graph template."""

    def test_produces_valid_graph_config(self) -> None:
        graph = build_negotiation_graph("gpt-4o", "gpt-4o")
        assert graph.name == "negotiation"
        assert graph.entry == "proposer"
        assert "proposer" in graph.nodes
        assert "responder" in graph.nodes
        assert "check_agreement" in graph.nodes
        assert graph.nodes["proposer"].type == NodeType.AGENT
        assert graph.nodes["responder"].type == NodeType.AGENT
        assert graph.nodes["check_agreement"].type == NodeType.CONDITION

    def test_has_correct_channels(self) -> None:
        graph = build_negotiation_graph("gpt-4o", "gpt-4o")
        assert "messages" in graph.state
        assert "agreement_status" in graph.state
        assert "negotiation_channel" in graph.state
        assert "negotiation_round" in graph.state

    def test_has_loop_edges(self) -> None:
        graph = build_negotiation_graph("gpt-4o", "gpt-4o")
        sources = [e.source for e in graph.edges]
        targets = [e.target for e in graph.edges]
        assert "proposer" in sources
        assert "responder" in sources
        assert "check_agreement" in sources
        assert "__end__" in str(targets)

    def test_compiles_successfully(self) -> None:
        graph = build_negotiation_graph("gpt-4o", "gpt-4o")
        compiler = GraphCompiler()
        compiled = compiler.compile(graph)
        assert compiled.entry_point == "proposer"
        assert len(compiled.nodes) == 3

    def test_custom_prompts(self) -> None:
        graph = build_negotiation_graph(
            "gpt-4o",
            "gpt-4o",
            proposer_prompt="Custom proposer",
            responder_prompt="Custom responder",
            max_rounds=10,
        )
        assert graph.nodes["proposer"].config["system_prompt"] == "Custom proposer"
        assert graph.nodes["responder"].config["system_prompt"] == "Custom responder"


class TestDebateGraph:
    """Tests for build_debate_graph template."""

    def test_with_judge(self) -> None:
        graph = build_debate_graph("gpt-4o", "gpt-4o", judge_model="gpt-4o")
        assert graph.name == "debate"
        assert graph.entry == "debater_a"
        assert "debater_a" in graph.nodes
        assert "debater_b" in graph.nodes
        assert "check_rounds" in graph.nodes
        assert "judge" in graph.nodes
        assert graph.nodes["debater_a"].type == NodeType.AGENT
        assert graph.nodes["judge"].type == NodeType.AGENT

    def test_without_judge(self) -> None:
        graph = build_debate_graph("gpt-4o", "gpt-4o")
        assert "judge" not in graph.nodes
        assert graph.nodes["debater_a"].type == NodeType.AGENT
        assert graph.nodes["debater_b"].type == NodeType.AGENT

    def test_with_judge_compiles(self) -> None:
        graph = build_debate_graph("gpt-4o", "gpt-4o", judge_model="gpt-4o")
        compiler = GraphCompiler()
        compiled = compiler.compile(graph)
        assert "judge" in compiled.nodes
        assert len(compiled.nodes) == 4

    def test_without_judge_compiles(self) -> None:
        graph = build_debate_graph("gpt-4o", "gpt-4o")
        compiler = GraphCompiler()
        compiled = compiler.compile(graph)
        assert "judge" not in compiled.nodes
        assert len(compiled.nodes) == 3

    def test_has_debate_channels(self) -> None:
        graph = build_debate_graph("gpt-4o", "gpt-4o")
        assert "messages" in graph.state
        assert "debate_round" in graph.state
        assert "max_debate_rounds" in graph.state

    def test_max_rounds_default(self) -> None:
        graph = build_debate_graph("gpt-4o", "gpt-4o", rounds=5)
        assert graph.state["max_debate_rounds"].default == 5


class TestTemplateImports:
    """Tests that template functions are importable."""

    def test_import_negotiation(self) -> None:
        from hecate.engine.templates import build_negotiation_graph

        assert callable(build_negotiation_graph)

    def test_import_debate(self) -> None:
        from hecate.engine.templates import build_debate_graph

        assert callable(build_debate_graph)
