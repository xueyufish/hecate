"""Tests for sequential pipeline and broadcast pipeline factory functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hecate.engine.graph_dsl import parse_graph
from hecate.engine.templates import build_broadcast_pipeline, build_sequential_pipeline
from hecate.engine.types import ChannelType, GraphConfig, NodeType

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "hecate" / "data" / "orchestration_templates"

STAGES_2 = [
    {"id": "researcher", "model": "gpt-4o", "system_prompt": "Research the topic."},
    {"id": "writer", "model": "gpt-4o", "system_prompt": "Write content."},
]

STAGES_3 = [
    {"id": "researcher", "model": "gpt-4o", "system_prompt": "Research the topic."},
    {"id": "writer", "model": "gpt-4o", "system_prompt": "Write content."},
    {"id": "reviewer", "model": "gpt-4o", "system_prompt": "Review content."},
]

PARTICIPANTS_3 = [
    {"id": "alice", "model": "gpt-4o", "system_prompt": "Creative thinker."},
    {"id": "bob", "model": "gpt-4o", "system_prompt": "Analytical thinker."},
    {"id": "charlie", "model": "gpt-4o", "system_prompt": "Critical thinker."},
]

MODERATOR = {"model": "gpt-4o", "system_prompt": "Moderate the discussion."}


# ---------------------------------------------------------------------------
# Sequential pipeline — basic
# ---------------------------------------------------------------------------


class TestBuildSequentialPipelineBasic:
    def test_node_count(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        assert len(graph.nodes) == 2

    def test_node_types(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        for node in graph.nodes.values():
            assert node.type == NodeType.AGENT

    def test_entry_point(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        assert graph.entry == "researcher"

    def test_edge_topology(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        assert len(graph.edges) == 2
        assert graph.edges[0].source == "researcher"
        assert graph.edges[0].target == "writer"
        assert graph.edges[1].source == "writer"
        assert graph.edges[1].target == "__end__"

    def test_channel_definitions(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        assert "messages" in graph.state
        assert graph.state["messages"].type == ChannelType.TOPIC
        assert "researcher_output" in graph.state
        assert graph.state["researcher_output"].type == ChannelType.LAST_VALUE

    def test_inter_stage_channel_wiring(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        researcher = graph.nodes["researcher"]
        writer = graph.nodes["writer"]
        assert "researcher_output" in researcher.config["channels"]["writable"]
        assert "researcher_output" in writer.config["channels"]["readable"]

    def test_shared_messages_channel(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        for node in graph.nodes.values():
            assert "messages" in node.config["channels"]["readable"]
            assert "messages" in node.config["channels"]["writable"]

    def test_graph_name(self):
        graph = build_sequential_pipeline(stages=STAGES_2)
        assert graph.name == "sequential-pipeline"

    def test_three_stage_middle_wiring(self):
        graph = build_sequential_pipeline(stages=STAGES_3)
        writer = graph.nodes["writer"]
        assert "researcher_output" in writer.config["channels"]["readable"]
        assert "writer_output" in writer.config["channels"]["writable"]


# ---------------------------------------------------------------------------
# Sequential pipeline — revision loop
# ---------------------------------------------------------------------------


class TestBuildSequentialPipelineRevision:
    def test_revision_adds_condition_node(self):
        graph = build_sequential_pipeline(
            stages=STAGES_2,
            revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"},
        )
        assert len(graph.nodes) == 3
        assert "check_revision" in graph.nodes
        assert graph.nodes["check_revision"].type == NodeType.CONDITION

    def test_revision_condition_edge(self):
        graph = build_sequential_pipeline(
            stages=STAGES_2,
            revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"},
        )
        cond_edge = graph.edges[-1]
        assert cond_edge.source == "check_revision"
        assert isinstance(cond_edge.target, dict)
        assert cond_edge.target["true"] == "writer"
        assert cond_edge.target["false"] == "__end__"

    def test_revision_status_channel(self):
        graph = build_sequential_pipeline(
            stages=STAGES_2,
            revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"},
        )
        assert "revision_status" in graph.state
        assert graph.state["revision_status"].type == ChannelType.LAST_VALUE

    def test_revision_target_reads_status(self):
        graph = build_sequential_pipeline(
            stages=STAGES_2,
            revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"},
        )
        writer = graph.nodes["writer"]
        assert "revision_status" in writer.config["channels"]["readable"]

    def test_last_stage_writes_revision_status(self):
        graph = build_sequential_pipeline(
            stages=STAGES_2,
            revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"},
        )
        writer = graph.nodes["writer"]
        assert "revision_status" in writer.config["channels"]["writable"]


# ---------------------------------------------------------------------------
# Sequential pipeline — validation
# ---------------------------------------------------------------------------


class TestBuildSequentialPipelineValidation:
    def test_minimum_stages(self):
        with pytest.raises(ValueError, match="at least 2"):
            build_sequential_pipeline(stages=[{"id": "a", "model": "m", "system_prompt": "p"}])

    def test_empty_stages(self):
        with pytest.raises(ValueError, match="at least 2"):
            build_sequential_pipeline(stages=[])

    def test_duplicate_ids(self):
        with pytest.raises(ValueError, match="Duplicate"):
            build_sequential_pipeline(
                stages=[
                    {"id": "agent", "model": "m", "system_prompt": "p1"},
                    {"id": "agent", "model": "m", "system_prompt": "p2"},
                ],
            )


# ---------------------------------------------------------------------------
# Broadcast pipeline — basic
# ---------------------------------------------------------------------------


class TestBuildBroadcastPipelineBasic:
    def test_node_count(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        assert len(graph.nodes) == 3

    def test_node_types(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        for node in graph.nodes.values():
            assert node.type == NodeType.AGENT

    def test_entry_point(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        assert graph.entry == "alice"

    def test_edge_topology(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        assert len(graph.edges) == 3
        assert graph.edges[0].source == "alice"
        assert graph.edges[0].target == "bob"
        assert graph.edges[1].source == "bob"
        assert graph.edges[1].target == "charlie"
        assert graph.edges[2].source == "charlie"
        assert graph.edges[2].target == "__end__"

    def test_shared_messages_channel(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        for node in graph.nodes.values():
            assert node.config["channels"]["readable"] == ["messages"]
            assert node.config["channels"]["writable"] == ["messages"]

    def test_graph_name(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        assert graph.name == "broadcast-pipeline"

    def test_only_messages_channel(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3)
        assert len(graph.state) == 1
        assert "messages" in graph.state


# ---------------------------------------------------------------------------
# Broadcast pipeline — moderator
# ---------------------------------------------------------------------------


class TestBuildBroadcastPipelineModerator:
    def test_moderator_adds_two_nodes(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3, moderator=MODERATOR)
        assert len(graph.nodes) == 5
        assert "moderator" in graph.nodes
        assert "moderator_summary" in graph.nodes

    def test_moderator_edge_topology(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3, moderator=MODERATOR)
        expected_order = ["moderator", "alice", "bob", "charlie", "moderator_summary"]
        for i, expected_id in enumerate(expected_order[:-1]):
            assert graph.edges[i].source == expected_id
            assert graph.edges[i].target == expected_order[i + 1]
        assert graph.edges[-1].source == "moderator_summary"
        assert graph.edges[-1].target == "__end__"

    def test_moderator_entry_point(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3, moderator=MODERATOR)
        assert graph.entry == "moderator"

    def test_moderator_shares_messages(self):
        graph = build_broadcast_pipeline(participants=PARTICIPANTS_3, moderator=MODERATOR)
        for node_id in ("moderator", "moderator_summary"):
            assert node_id in graph.nodes
            assert graph.nodes[node_id].config["channels"]["readable"] == ["messages"]
            assert graph.nodes[node_id].config["channels"]["writable"] == ["messages"]


# ---------------------------------------------------------------------------
# Broadcast pipeline — validation
# ---------------------------------------------------------------------------


class TestBuildBroadcastPipelineValidation:
    def test_minimum_participants(self):
        with pytest.raises(ValueError, match="at least 2"):
            build_broadcast_pipeline(
                participants=[{"id": "a", "model": "m", "system_prompt": "p"}],
            )

    def test_empty_participants(self):
        with pytest.raises(ValueError, match="at least 2"):
            build_broadcast_pipeline(participants=[])

    def test_duplicate_ids(self):
        with pytest.raises(ValueError, match="Duplicate"):
            build_broadcast_pipeline(
                participants=[
                    {"id": "agent", "model": "m", "system_prompt": "p1"},
                    {"id": "agent", "model": "m", "system_prompt": "p2"},
                ],
            )


# ---------------------------------------------------------------------------
# JSON template tests
# ---------------------------------------------------------------------------


class TestSequentialPipelineJsonTemplate:
    def _load_template(self) -> dict:
        path = TEMPLATES_DIR / "sequential-pipeline.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _graph_from_template(self) -> GraphConfig:
        data = self._load_template()
        for key in ("category", "description"):
            data.pop(key, None)
        return parse_graph(data)

    def test_template_loads(self):
        graph = self._graph_from_template()
        assert graph is not None

    def test_template_structure(self):
        graph = self._graph_from_template()
        assert len(graph.nodes) == 4
        assert "researcher" in graph.nodes
        assert "writer" in graph.nodes
        assert "reviewer" in graph.nodes
        assert "check_revision" in graph.nodes
        assert graph.nodes["check_revision"].type == NodeType.CONDITION


class TestBroadcastPipelineJsonTemplate:
    def _load_template(self) -> dict:
        path = TEMPLATES_DIR / "broadcast-pipeline.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _graph_from_template(self) -> GraphConfig:
        data = self._load_template()
        for key in ("category", "description"):
            data.pop(key, None)
        return parse_graph(data)

    def test_template_loads(self):
        graph = self._graph_from_template()
        assert graph is not None

    def test_template_structure(self):
        graph = self._graph_from_template()
        assert len(graph.nodes) == 5
        assert "moderator" in graph.nodes
        assert "moderator_summary" in graph.nodes
        assert "alice" in graph.nodes
        assert "bob" in graph.nodes
        assert "charlie" in graph.nodes
