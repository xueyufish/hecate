"""Tests for 1.3.21③ Send-style dynamic fan-out in the Pregel runtime.

Validates end-to-end flow: a CONDITION node carrying a ``fanout`` config
emits ``_dispatch`` packets which the engine turns into per-branch
invocations with seeded sub-channels. The MERGE-like target collects
per-branch outputs through its sub-channel.

The tests use lightweight stub workers (no LLM calls, deterministic).
"""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.pregel import FanoutLimitError, PregelRuntime
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


class _PlannerWorker(Worker):
    """Single worker that branches by ``node_id`` to simulate planner + branch.

    The runtime dispatches one worker instance to every scheduled node, so
    the planner and the branch target share this class. ``planner_node``
    names the planner; everything else echoes.
    """

    def __init__(self, planner_node: str = "planner", branch_node: str = "branch") -> None:
        self.planner_node = planner_node
        self.branch_node = branch_node

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        if node_id == self.planner_node:
            return self._plan(node_id, node_config, channel_snapshot)
        return self._echo(node_id, node_config, channel_snapshot, execution_context)

    def _plan(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        fanout_cfg = node_config.get("fanout") or {}
        over = fanout_cfg.get("over", "items")
        target = fanout_cfg.get("target", self.branch_node)
        state_key = fanout_cfg.get("state_key", "item")
        items = channel_snapshot.get(over, []) or []
        if not isinstance(items, list):
            items = [items]
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "_dispatch": [{"node": target, "state": {state_key: v}} for v in items],
                "messages": [f"{node_id}_planned_{len(items)}"],
            },
        )

    def _echo(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None,
    ) -> WorkerResult:
        sub_channel = (execution_context or {}).get("_fanout_sub_channel", "")
        planner = (execution_context or {}).get("_fanout_planner", "")
        branch_index = (execution_context or {}).get("_fanout_branch_index", -1)
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "messages": [f"branch@{planner}.{branch_index}:{sub_channel}"],
            },
        )


class _EchoBranchWorker(Worker):
    """Branch worker that echoes its slice into the messages channel.

    Kept for backward compatibility with tests that only need the branch
    half of a plan (planner is exercised through ``_PlannerWorker``).
    """

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        sub_channel = (execution_context or {}).get("_fanout_sub_channel", "")
        planner = (execution_context or {}).get("_fanout_planner", "")
        branch_index = (execution_context or {}).get("_fanout_branch_index", -1)
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "messages": [f"branch@{planner}.{branch_index}:{sub_channel}"],
            },
        )


def _make_dynamic_fanout_graph() -> CompiledGraph:
    """Planner -> branch (target node, called N times) -> END.

    Topology intentionally has no static edges out of the planner or the
    branch target — dynamic dispatch should not collide with static edge
    resolution. The branch node itself routes to ``__end__`` via its
    own static edge so the graph terminates.
    """
    return CompiledGraph(
        nodes={
            "planner": NodeConfig(
                id="planner",
                type=NodeType.CONDITION,
                config={
                    "fanout": {
                        "over": "items",
                        "target": "branch",
                        "state_key": "item",
                    }
                },
            ),
            "branch": NodeConfig(
                id="branch",
                type=NodeType.CONVERSATION,
                config={"model": "test", "system_prompt": "branch"},
            ),
        },
        edges=[
            # Planner's static edge would be the legacy single-target goto;
            # keep an edge to __end__ so the dispatch semantics are clear:
            # the dynamic plan overrides this for the planner's frame.
            Edge(source="planner", target="__end__"),
            Edge(source="branch", target="__end__"),
        ],
        channels={
            "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
            "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
        },
        entry_point="planner",
        name="test-dynamic-fanout",
    )


def _make_multi_planner_graph() -> CompiledGraph:
    """Two planners, single shared target node, no merge.

    Confirms that the same target node can be invoked N times in one
    superstep (one per branch_index) without dedup.
    """
    return CompiledGraph(
        nodes={
            "p1": NodeConfig(
                id="p1",
                type=NodeType.CONDITION,
                config={"fanout": {"over": "left", "target": "collect", "state_key": "v"}},
            ),
            "p2": NodeConfig(
                id="p2",
                type=NodeType.CONDITION,
                config={"fanout": {"over": "right", "target": "collect", "state_key": "v"}},
            ),
            "collect": NodeConfig(
                id="collect",
                type=NodeType.CONVERSATION,
                config={"model": "test", "system_prompt": "collect"},
            ),
        },
        edges=[
            Edge(source="p1", target="__end__"),
            Edge(source="p2", target="__end__"),
            Edge(source="collect", target="__end__"),
        ],
        channels={
            "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
            "left": ChannelDef(type=ChannelType.TOPIC, default=[]),
            "right": ChannelDef(type=ChannelType.TOPIC, default=[]),
        },
        entry_point="p1",  # doesn't matter; the test seeds both planners
        name="test-dynamic-fanout-multi",
    )


class TestDynamicFanoutDispatch:
    """End-to-end dynamic fan-out engine behavior (1.3.21③)."""

    @pytest.mark.asyncio
    async def test_planner_emits_dispatch_for_each_over_element(self):
        graph = _make_dynamic_fanout_graph()
        runtime = PregelRuntime(graph, _PlannerWorker(), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        final_state: dict = {}
        async for ev in runtime.execute(session_id, initial_input={"items": ["a", "b", "c"]}):
            if ev["type"] == "values":
                final_state = ev["state"]

        # Branch worker echoed N invocations into ``messages``.
        branch_msgs = [m for m in final_state["messages"] if m.startswith("branch@planner.")]
        assert len(branch_msgs) == 3

    @pytest.mark.asyncio
    async def test_branch_subchannels_are_isolated(self):
        """Each branch invocation has its own sub-channel; no cross-talk."""
        graph = _make_dynamic_fanout_graph()
        runtime = PregelRuntime(graph, _PlannerWorker(), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        final_state: dict = {}
        async for ev in runtime.execute(session_id, initial_input={"items": ["x", "y"]}):
            if ev["type"] == "values":
                final_state = ev["state"]

        branch_msgs = [m for m in final_state["messages"] if m.startswith("branch@planner.")]
        assert len(branch_msgs) == 2
        # Sub-channels share the planner prefix but differ in index.
        assert any("_fanout__planner__idx0" in m for m in branch_msgs)
        assert any("_fanout__planner__idx1" in m for m in branch_msgs)
        # Each branch sees its own sub-channel name; never another's.
        idx0_msgs = [m for m in branch_msgs if "_fanout__planner__idx0" in m]
        idx1_msgs = [m for m in branch_msgs if "_fanout__planner__idx1" in m]
        assert idx0_msgs and idx1_msgs
        assert not any("_fanout__planner__idx1" in m for m in idx0_msgs)
        assert not any("_fanout__planner__idx0" in m for m in idx1_msgs)

    @pytest.mark.asyncio
    async def test_node_events_carry_branch_identity(self):
        graph = _make_dynamic_fanout_graph()
        store = InMemoryEventStore()
        runtime = PregelRuntime(graph, _PlannerWorker(), InMemoryCheckpointStore(), event_store=store)

        session_id = uuid.uuid4()
        async for _ in runtime.execute(session_id, initial_input={"items": ["a", "b"]}):
            pass

        events = await store.get_events(session_id)
        branch_starts = [
            e
            for e in events
            if e.event_type == EventType.NODE_START
            and e.node_id == "branch"
            and e.payload.get("fanout_source") == "planner"
        ]
        assert len(branch_starts) == 2
        indices = sorted(e.payload["branch_index"] for e in branch_starts)
        assert indices == [0, 1]

    @pytest.mark.asyncio
    async def test_superstep_invocation_cap_enforced(self):
        """superstep-wide cap fires when dispatch exceeds ceiling."""
        from hecate.runtime.types import ABSOLUTE_MAX_FANOUT, DEFAULT_MAX_INVOCATIONS_PER_SUPERSTEP

        graph = CompiledGraph(
            nodes={
                "p1": NodeConfig(
                    id="p1",
                    type=NodeType.CONDITION,
                    # max_fanout raised beyond the superstep cap so the
                    # superstep cap is the binding constraint.
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "v",
                            "max_fanout": ABSOLUTE_MAX_FANOUT,
                        }
                    },
                ),
                "branch": NodeConfig(
                    id="branch",
                    type=NodeType.CONVERSATION,
                    config={},
                ),
            },
            edges=[
                Edge(source="p1", target="__end__"),
                Edge(source="branch", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="p1",
            name="test-cap",
        )
        runtime = PregelRuntime(graph, _PlannerWorker(planner_node="p1"), InMemoryCheckpointStore())

        too_many = DEFAULT_MAX_INVOCATIONS_PER_SUPERSTEP + 1
        session_id = uuid.uuid4()
        with pytest.raises(FanoutLimitError, match="superstep"):
            async for _ in runtime.execute(session_id, initial_input={"items": list(range(too_many))}):
                pass

    @pytest.mark.asyncio
    async def test_per_node_max_fanout_enforced(self):
        """A node-level max_fanout caps the dispatch size before superstep cap."""
        graph = CompiledGraph(
            nodes={
                "p1": NodeConfig(
                    id="p1",
                    type=NodeType.CONDITION,
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "v",
                            "max_fanout": 4,
                        }
                    },
                ),
                "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
            },
            edges=[
                Edge(source="p1", target="__end__"),
                Edge(source="branch", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="p1",
            name="test-per-node-cap",
        )
        runtime_a = PregelRuntime(graph, _PlannerWorker(planner_node="p1"), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        # 8 items > 4 ceiling; should fail at planner:per_node level.
        with pytest.raises(FanoutLimitError, match="per_node"):
            async for _ in runtime_a.execute(session_id, initial_input={"items": list(range(8))}):
                pass

        # Sanity: with 4 items we fit under the ceiling. Use a fresh runtime
        # so the TOPIC items channel doesn't bleed session-1 leftovers.
        runtime_b = PregelRuntime(graph, _PlannerWorker(planner_node="p1"), InMemoryCheckpointStore())
        session_id_2 = uuid.uuid4()
        final_state: dict = {}
        async for ev in runtime_b.execute(session_id_2, initial_input={"items": list(range(4))}):
            if ev["type"] == "values":
                final_state = ev["state"]
        branch_msgs = [m for m in final_state["messages"] if m.startswith("branch@p1.")]
        assert len(branch_msgs) == 4

    @pytest.mark.asyncio
    async def test_collect_mode_continues_on_branch_error(self):
        """``on_branch_error: collect`` keeps the batch alive on failure."""

        class _FailingBranch(Worker):
            def __init__(self) -> None:
                self.fail_indices: set[int] = set()

            async def execute(
                self,
                node_id: str,
                node_config: dict,
                channel_snapshot: dict,
                execution_context: dict | None = None,
            ) -> WorkerResult:
                # The planner also runs through this worker (one-worker-per-runtime);
                # let it emit a normal dispatch plan without failing.
                if "fanout" in (node_config or {}):
                    items = channel_snapshot.get("items", []) or []
                    if not isinstance(items, list):
                        items = [items]
                    return WorkerResult(
                        node_id=node_id,
                        channel_updates={
                            "_dispatch": [{"node": "branch", "state": {"v": v}} for v in items],
                            "messages": [f"{node_id}_planned_{len(items)}"],
                        },
                    )
                idx = (execution_context or {}).get("_fanout_branch_index", -1)
                if idx in self.fail_indices:
                    return WorkerResult(
                        node_id=node_id,
                        channel_updates={},
                        error=RuntimeError(f"branch {idx} boom"),
                    )
                return WorkerResult(
                    node_id=node_id,
                    channel_updates={"messages": [f"ok:{idx}"]},
                )

        graph = CompiledGraph(
            nodes={
                "p1": NodeConfig(
                    id="p1",
                    type=NodeType.CONDITION,
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "v",
                        },
                        "on_branch_error": "collect",
                    },
                ),
                "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
            },
            edges=[
                Edge(source="p1", target="__end__"),
                Edge(source="branch", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="p1",
            name="test-collect",
        )
        worker = _FailingBranch()
        worker.fail_indices = {1}
        runtime = PregelRuntime(graph, worker, InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        final_state: dict = {}
        async for ev in runtime.execute(session_id, initial_input={"items": [10, 20, 30]}):
            if ev["type"] == "values":
                final_state = ev["state"]

        # Branch 1 failed, branches 0 and 2 succeeded — only 2 messages.
        branch_msgs = [m for m in final_state["messages"] if m.startswith("ok:")]
        assert len(branch_msgs) == 2
        # The error branch's sub-channel holds the __branch_error__ marker.
        snapshot = runtime._channel_manager.snapshot()
        assert "__branch_error__" in snapshot.get("_fanout__p1__idx1", {})
        # The other sub-channels hold normal worker output.
        assert "_fanout__p1__idx0" in snapshot
        assert "_fanout__p1__idx2" in snapshot

    @pytest.mark.asyncio
    async def test_fail_fast_default_raises_on_branch_error(self):
        """Default behavior (no on_branch_error) raises on any branch error."""

        class _AlwaysFailingBranch(Worker):
            async def execute(
                self,
                node_id: str,
                node_config: dict,
                channel_snapshot: dict,
                execution_context: dict | None = None,
            ) -> WorkerResult:
                return WorkerResult(
                    node_id=node_id,
                    channel_updates={},
                    error=RuntimeError("kaboom"),
                )

        graph = CompiledGraph(
            nodes={
                "p1": NodeConfig(
                    id="p1",
                    type=NodeType.CONDITION,
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "v",
                        }
                    },
                ),
                "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
            },
            edges=[
                Edge(source="p1", target="__end__"),
                Edge(source="branch", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="p1",
            name="test-fail-fast",
        )
        runtime = PregelRuntime(graph, _AlwaysFailingBranch(), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        with pytest.raises(RuntimeError, match="kaboom"):
            async for _ in runtime.execute(session_id, initial_input={"items": ["a"]}):
                pass

    @pytest.mark.asyncio
    async def test_interrupt_after_planner_pauses_with_plan(self):
        """1.3.21③ + ①: planner on interrupt_after pauses after committing plan."""
        graph = CompiledGraph(
            nodes={
                "planner": NodeConfig(
                    id="planner",
                    type=NodeType.CONDITION,
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "item",
                        }
                    },
                ),
                "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
            },
            edges=[
                Edge(source="planner", target="__end__"),
                Edge(source="branch", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="planner",
            interrupt_after=["planner"],
            name="test-interrupt-after-planner",
        )
        runtime = PregelRuntime(graph, _PlannerWorker(), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        interrupt_value: dict | None = None
        async for ev in runtime.execute(session_id, initial_input={"items": ["a", "b"]}):
            if ev["type"] == "interrupt":
                interrupt_value = ev["value"]

        assert interrupt_value is not None
        assert interrupt_value["kind"] == "declarative"
        assert interrupt_value["phase"] == "after"
        assert "planner" in interrupt_value["nodes"]
        # The plan was committed; the channel state still carries it.
        snapshot = runtime._channel_manager.snapshot()
        assert snapshot.get("_dispatch") == [
            {"node": "branch", "state": {"item": "a"}},
            {"node": "branch", "state": {"item": "b"}},
        ]

    @pytest.mark.asyncio
    async def test_dynamic_merge_aggregates_per_branch_index(self):
        """MERGE on a dynamic fan-out source emits a dict keyed by branch_index."""
        graph = CompiledGraph(
            nodes={
                "planner": NodeConfig(
                    id="planner",
                    type=NodeType.CONDITION,
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "v",
                        }
                    },
                ),
                "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
                "merge": NodeConfig(
                    id="merge",
                    type=NodeType.MERGE,
                    config={"fan_out_source": "planner", "output_channel": "merged"},
                ),
            },
            edges=[
                Edge(source="planner", target="__end__"),
                Edge(source="branch", target="merge"),
                Edge(source="merge", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "merged": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
            },
            entry_point="planner",
            name="test-dynamic-merge",
        )
        runtime = PregelRuntime(graph, _PlannerWorker(), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        final_state: dict = {}
        async for ev in runtime.execute(session_id, initial_input={"items": ["a", "b", "c"]}):
            if ev["type"] == "values":
                final_state = ev["state"]

        # MERGE aggregated branches 0..2 from sub-channels into merged.
        merged = final_state["merged"]
        assert set(merged.keys()) == {"0", "1", "2"}
        for key, value in merged.items():
            # Each branch wrote ``branch@planner.{idx}:_fanout__planner__idx{idx}``
            assert any(m.startswith(f"branch@planner.{key}:") for m in value["messages"])

    @pytest.mark.asyncio
    async def test_static_fanout_merge_regression(self):
        """Static FAN_OUT → MERGE contract is bit-identical after the refactor."""
        graph = CompiledGraph(
            nodes={
                "fanout": NodeConfig(id="fanout", type=NodeType.FAN_OUT, config={"branches": ["a", "b"]}),
                "a": NodeConfig(id="a", type=NodeType.CONVERSATION, config={}),
                "b": NodeConfig(id="b", type=NodeType.CONVERSATION, config={}),
                "merge": NodeConfig(
                    id="merge",
                    type=NodeType.MERGE,
                    config={"fan_out_source": "fanout", "output_channel": "merged"},
                ),
            },
            edges=[
                Edge(source="fanout", target="a"),
                Edge(source="fanout", target="b"),
                Edge(source="a", target="merge"),
                Edge(source="b", target="merge"),
                Edge(source="merge", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "merged": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
            },
            entry_point="fanout",
            name="test-static-regression",
        )

        class _Simple(Worker):
            async def execute(
                self,
                node_id: str,
                node_config: dict,
                channel_snapshot: dict,
                execution_context: dict | None = None,
            ) -> WorkerResult:
                return WorkerResult(
                    node_id=node_id,
                    channel_updates={"messages": [f"{node_id}_out"]},
                )

        runtime = PregelRuntime(graph, _Simple(), InMemoryCheckpointStore())
        session_id = uuid.uuid4()
        final_state: dict = {}
        async for ev in runtime.execute(session_id):
            if ev["type"] == "values":
                final_state = ev["state"]

        # Static contract: {branch_id: sub_channel_value}.
        assert set(final_state["merged"].keys()) == {"a", "b"}
        assert final_state["merged"]["a"]["messages"] == ["a_out"]
        assert final_state["merged"]["b"]["messages"] == ["b_out"]
        """1.3.21③ + ①: target node in interrupt_before pauses before all branches."""
        graph = CompiledGraph(
            nodes={
                "planner": NodeConfig(
                    id="planner",
                    type=NodeType.CONDITION,
                    config={
                        "fanout": {
                            "over": "items",
                            "target": "branch",
                            "state_key": "item",
                        }
                    },
                ),
                "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
            },
            edges=[
                Edge(source="planner", target="__end__"),
                Edge(source="branch", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "items": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="planner",
            interrupt_before=["branch"],
            name="test-interrupt-before-target",
        )
        runtime = PregelRuntime(graph, _PlannerWorker(), InMemoryCheckpointStore())

        session_id = uuid.uuid4()
        interrupt_value: dict | None = None
        async for ev in runtime.execute(session_id, initial_input={"items": ["a", "b", "c"]}):
            if ev["type"] == "interrupt":
                interrupt_value = ev["value"]

        assert interrupt_value is not None
        assert interrupt_value["kind"] == "declarative"
        assert interrupt_value["phase"] == "before"
        assert "branch" in interrupt_value["nodes"]
        # No branch ran — only the planner wrote anything to messages.
        snapshot = runtime._channel_manager.snapshot()
        assert not any(isinstance(v, str) and v.startswith("branch@planner.") for v in snapshot.get("messages", []))
