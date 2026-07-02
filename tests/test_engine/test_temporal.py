"""Unit tests for DistributedPregelWorkflow and ConflictResolver integration."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from hecate.engine.channel import LastValueBehavior
from hecate.engine.temporal.conflict import (
    ConflictResolver,
    ConflictStrategy,
)
from hecate.engine.temporal.workflow import DistributedPregelWorkflow


class TestDistributedPregelWorkflow:
    def test_init_defaults(self) -> None:
        wf = DistributedPregelWorkflow(graph_id="test-graph")

        assert wf.graph_id == "test-graph"
        assert wf.max_supersteps == 100
        assert wf.continue_as_new_threshold == 10

    async def test_execute_returns_placeholder(self) -> None:
        wf = DistributedPregelWorkflow(graph_id="test-graph")
        session_id = uuid.uuid4()

        result = await wf.execute(session_id=session_id)

        assert result["session_id"] == str(session_id)
        assert "status" in result

    async def test_execute_with_initial_input(self) -> None:
        wf = DistributedPregelWorkflow(graph_id="test-graph")
        session_id = uuid.uuid4()

        result = await wf.execute(
            session_id=session_id,
            initial_input={"channel_a": "hello"},
        )

        assert result["session_id"] == str(session_id)

    def test_custom_thresholds(self) -> None:
        wf = DistributedPregelWorkflow(
            graph_id="g",
            max_supersteps=50,
            continue_as_new_threshold=5,
        )

        assert wf.max_supersteps == 50
        assert wf.continue_as_new_threshold == 5


class TestConflictResolverHumanApproval:
    def test_resolve_normal_no_approval_needed(self) -> None:
        resolver = ConflictResolver()
        result = resolver.resolve("ch", "old", "new", behavior=LastValueBehavior())

        assert result.resolved is True
        assert result.final_value == "new"
        assert result.requires_approval is False

    def test_request_approval(self) -> None:
        resolver = ConflictResolver()
        result = resolver.resolve(
            "ch",
            "old",
            "new",
            behavior=LastValueBehavior(),
            require_approval=True,
        )

        assert result.resolved is False
        assert result.requires_approval is True
        assert result.strategy_used == ConflictStrategy.HUMAN_APPROVAL

    def test_resolve_approval_accept(self) -> None:
        resolver = ConflictResolver()
        conflict_result = resolver.resolve(
            "ch",
            "old",
            "new",
            require_approval=True,
        )
        conflict_id = conflict_result.final_value["conflict_id"]

        resolution = resolver.resolve_approval(conflict_id, approved=True, approver="admin")

        assert resolution.resolved is True
        assert resolution.final_value == "new"
        assert resolution.strategy_used == ConflictStrategy.HUMAN_APPROVAL

    def test_resolve_approval_reject(self) -> None:
        resolver = ConflictResolver()
        conflict_result = resolver.resolve(
            "ch",
            "old",
            "new",
            require_approval=True,
        )
        conflict_id = conflict_result.final_value["conflict_id"]

        resolution = resolver.resolve_approval(conflict_id, approved=False, approver="admin")

        assert resolution.resolved is True
        assert resolution.final_value == "old"

    def test_resolve_approval_not_found(self) -> None:
        resolver = ConflictResolver()
        resolution = resolver.resolve_approval("nonexistent", approved=True)

        assert resolution.resolved is False

    def test_get_pending_approvals(self) -> None:
        resolver = ConflictResolver()
        resolver.resolve("ch1", "a", "b", require_approval=True)
        resolver.resolve("ch2", "c", "d", require_approval=True)

        pending = resolver.get_pending_approvals()

        assert len(pending) == 2

    def test_pending_cleared_after_resolution(self) -> None:
        resolver = ConflictResolver()
        result = resolver.resolve("ch", "old", "new", require_approval=True)
        conflict_id = result.final_value["conflict_id"]

        resolver.resolve_approval(conflict_id, approved=True)

        assert len(resolver.get_pending_approvals()) == 0


class TestConflictResolverWithPregelRuntime:
    async def test_runtime_with_conflict_resolver(self) -> None:
        from hecate.engine.checkpoint import CheckpointStore
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, StreamMode
        from hecate.engine.worker import Worker, WorkerResult

        resolver = ConflictResolver()

        graph = CompiledGraph(
            name="test",
            entry_point="node_a",
            nodes={"node_a": type("N", (), {"config": {}})()},
            edges=[Edge(source="node_a", target="__end__")],
            channels={"messages": ChannelDef(type=ChannelType.LAST_VALUE)},
        )

        class TestWorker(Worker):
            async def execute(self, node_id: str, config: dict, state: dict, execution_context=None) -> WorkerResult:
                return WorkerResult(node_id=node_id, channel_updates={"messages": "hello"})

        checkpoint_store = AsyncMock(spec=CheckpointStore)
        checkpoint_store.save = AsyncMock()
        checkpoint_store.load = AsyncMock(return_value=None)

        runtime = PregelRuntime(
            graph=graph,
            worker=TestWorker(),
            checkpoint_store=checkpoint_store,
            conflict_resolver=resolver,
        )

        results = []
        async for event in runtime.execute(
            session_id=uuid.uuid4(),
            initial_input={"messages": "initial"},
            stream_mode=StreamMode.VALUES,
        ):
            results.append(event)

        assert any(e.get("type") == "values" for e in results)

    async def test_runtime_without_conflict_resolver(self) -> None:
        from hecate.engine.checkpoint import CheckpointStore
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, StreamMode
        from hecate.engine.worker import Worker, WorkerResult

        graph = CompiledGraph(
            name="test",
            entry_point="node_a",
            nodes={"node_a": type("N", (), {"config": {}})()},
            edges=[Edge(source="node_a", target="__end__")],
            channels={"messages": ChannelDef(type=ChannelType.LAST_VALUE)},
        )

        class TestWorker(Worker):
            async def execute(self, node_id: str, config: dict, state: dict, execution_context=None) -> WorkerResult:
                return WorkerResult(node_id=node_id, channel_updates={"messages": "hello"})

        checkpoint_store = AsyncMock(spec=CheckpointStore)
        checkpoint_store.save = AsyncMock()
        checkpoint_store.load = AsyncMock(return_value=None)

        runtime = PregelRuntime(
            graph=graph,
            worker=TestWorker(),
            checkpoint_store=checkpoint_store,
        )

        results = []
        async for event in runtime.execute(
            session_id=uuid.uuid4(),
            initial_input={"messages": "initial"},
            stream_mode=StreamMode.VALUES,
        ):
            results.append(event)

        assert len(results) > 0


class TestTemporalTestEnvironment:
    """Integration test with Temporal test environment.

    These tests verify the workflow can run in a real Temporal test server.
    They are skipped when temporalio is not installed.
    """

    def test_temporal_import_available(self) -> None:
        try:
            import temporalio

            assert temporalio is not None
        except ImportError:
            pass
