from __future__ import annotations

from hecate.runtime.evidence import EvidenceTracker


class TestEvidenceCapture:
    def test_capture_success_record(self) -> None:
        tracker = EvidenceTracker(session_id="sess-1")
        record = tracker.capture(
            tool_name="web_search",
            arguments={"query": "hecate"},
            raw_content="results page",
            node_id="tool",
            superstep=2,
        )
        assert record.tool_name == "web_search"
        assert record.tool_arguments == {"query": "hecate"}
        assert record.raw_content == "results page"
        assert record.is_error is False
        assert record.importance == 0.5
        assert record.provenance == {"node_id": "tool", "superstep": 2}
        assert record.session_id == "sess-1"
        assert len(tracker) == 1

    def test_capture_error_boosts_importance(self) -> None:
        tracker = EvidenceTracker()
        record = tracker.capture(tool_name="t", raw_content="boom", is_error=True)
        assert record.is_error is True
        assert record.importance == 0.6

    def test_capture_dict_payload_stores_normalized(self) -> None:
        tracker = EvidenceTracker()
        payload = {"rows": [1, 2, 3]}
        record = tracker.capture(tool_name="sql", raw_content=payload)
        assert record.normalized_content == payload
        assert "rows" in record.raw_content

    def test_capture_trims_oversized_content(self) -> None:
        tracker = EvidenceTracker()
        record = tracker.capture(tool_name="read_file", raw_content="x" * 50_000)
        assert len(record.raw_content) <= 8_000

    def test_capture_never_raises(self) -> None:
        tracker = EvidenceTracker()
        record = tracker.capture(tool_name="t", arguments={"bad": {1, 2}}, raw_content=object())
        assert record is not None


class TestReReferenceBoost:
    def test_match_existing_boosts_importance(self) -> None:
        tracker = EvidenceTracker()
        first = tracker.capture(tool_name="search", arguments={"q": "x"})
        assert first.importance == 0.5

        matched = tracker.match_existing("search", {"q": "x"})
        assert matched is first
        assert matched.references == 2
        assert matched.importance == 0.7

        second = tracker.capture(tool_name="search", arguments={"q": "x"}, reused=True)
        # The prior record keeps its match boost; the repeated call itself
        # is captured with the explicit-reuse bonus.
        assert first.importance == 0.7
        assert second.importance == 0.6
        assert len(tracker) == 2

    def test_match_existing_no_match_returns_none(self) -> None:
        tracker = EvidenceTracker()
        assert tracker.match_existing("nope", {}) is None

    def test_argument_order_insensitive_signature(self) -> None:
        tracker = EvidenceTracker()
        tracker.capture(tool_name="t", arguments={"a": 1, "b": 2})
        assert tracker.match_existing("t", {"b": 2, "a": 1}) is not None


class TestSnapshot:
    def test_snapshot_returns_all(self) -> None:
        tracker = EvidenceTracker()
        tracker.capture(tool_name="a")
        tracker.capture(tool_name="b")
        assert len(tracker.snapshot()) == 2

    def test_snapshot_filters_by_importance(self) -> None:
        tracker = EvidenceTracker()
        tracker.capture(tool_name="low")
        tracker.capture(tool_name="high", is_error=True, reused=True)  # 0.7
        assert [r.tool_name for r in tracker.snapshot(min_importance=0.7)] == ["high"]

    def test_session_id_property(self) -> None:
        assert EvidenceTracker(session_id="abc").session_id == "abc"


class TestPregelEvidenceInjection:
    """PregelRuntime injects the evidence tracker into execution context."""

    async def test_tracker_reaches_workers_via_context(self) -> None:
        import uuid as uuid_mod

        from hecate.runtime.checkpoint import InMemoryCheckpointStore
        from hecate.runtime.pregel import PregelRuntime
        from hecate.runtime.types import (
            ChannelDef,
            ChannelType,
            CompiledGraph,
            Edge,
            NodeConfig,
            NodeType,
            WorkerResult,
        )
        from hecate.runtime.worker import Worker

        class CaptureWorker(Worker):
            def __init__(self) -> None:
                self.ctx: dict | None = None

            async def execute(self, node_id, node_config, channel_snapshot, execution_context=None):
                self.ctx = execution_context
                tracker = execution_context.get("evidence_tracker") if execution_context else None
                if tracker is not None:
                    tracker.capture(tool_name="probe", raw_content="captured")
                return WorkerResult(node_id=node_id, channel_updates={"messages": ["done"]})

        graph = CompiledGraph(
            nodes={"A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={})},
            edges=[Edge(source="A", target="__end__")],
            channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry_point="A",
            name="evidence-injection",
        )
        worker = CaptureWorker()
        tracker = EvidenceTracker(session_id="sess-e2e")
        runtime = PregelRuntime(
            graph=graph,
            worker=worker,
            checkpoint_store=InMemoryCheckpointStore(),
            evidence_tracker=tracker,
        )
        async for _ in runtime.execute(session_id=uuid_mod.uuid4(), initial_input={"messages": []}):
            pass
        assert worker.ctx is not None
        assert worker.ctx["evidence_tracker"] is tracker
        assert len(tracker) == 1
        assert tracker.records[0].tool_name == "probe"
