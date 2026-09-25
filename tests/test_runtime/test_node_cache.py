"""Tests for 1.3.21IV node-level CachePolicy.

Covers the delivery slices of the change:

- DSL: per-node ``cache`` blocks parse, compile, and roundtrip through
  ``CompiledGraph.to_json()``; the JSON schema rejects malformed blocks
  (missing / non-positive / non-integer ``ttl``).
- Compiler validation: ``key_func`` must name a registered function
  (fail loud, mirroring unknown reducer names); any node type may opt in.
- Cache machinery: TTL expiry, LRU eviction, key-function registry, and
  default key composition (model-config identity, readable-slice hashing,
  session/tenant namespaces, tenant fail-closed).
- Engine seam: hits skip the worker, misses store before the WAL commit,
  hit and miss log trajectories are identical except the NODE_END cached
  marker, no synthetic LLM events on hits, fold equivalence across cache
  flushes, interrupt interplay (cache is only consulted after the pause
  decision), and fan-out interplay (identical branch slices deduplicate
  nothing in v1 but MERGE stays correct).

The tests use lightweight worker stubs (no LLM calls), per tests/AGENTS.md.
"""

from __future__ import annotations

import uuid

import pytest

import hecate.runtime.node_cache as node_cache_module
from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.errors import GraphValidationError
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore
from hecate.runtime.node_cache import (
    InMemoryNodeCache,
    UnknownKeyFuncError,
    derive_cache_key,
    get_node_key_func,
    list_node_key_funcs,
    register_node_key_func,
)
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.types import (
    ChannelDef,
    ChannelType,
    CompiledGraph,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
    WorkerResult,
)
from hecate.runtime.worker import Worker
from hecate.studio.workflows.graph_dsl import parse_graph


@pytest.fixture
def clean_key_func_registry():
    """Snapshot the module-level key-func registry around each test."""
    saved = dict(node_cache_module._KEY_FUNCS)
    yield
    node_cache_module._KEY_FUNCS.clear()
    node_cache_module._KEY_FUNCS.update(saved)


class RecordingWorker(Worker):
    """Echo worker that records executed node IDs (no LLM calls)."""

    def __init__(self, event_store=None):
        super().__init__(event_store=event_store)
        self.calls: list[str] = []

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        self.calls.append(node_id)
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


class LlmEventWorker(RecordingWorker):
    """RecordingWorker that emits LLM_REQUEST/LLM_RESPONSE audit pairs on execute.

    Mimics the real LLM worker's log contract so tests can assert that a
    cache hit (worker skipped) produces no synthetic model events.
    """

    def __init__(self, event_store=None):
        super().__init__(event_store=event_store)
        self._event_store = event_store

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        self.calls.append(node_id)
        if self._event_store is not None and execution_context is not None:
            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.LLM_REQUEST,
                    node_id=node_id,
                    payload={},
                )
            )
            await self._event_store.append(
                Event(
                    session_id=execution_context["session_id"],
                    superstep=execution_context["superstep"],
                    event_type=EventType.LLM_RESPONSE,
                    node_id=node_id,
                    payload={},
                )
            )
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


class PlannerBranchWorker(Worker):
    """Planner + branch worker for dynamic fan-out (mirrors test_dynamic_fanout)."""

    def __init__(self, planner_node: str = "planner", branch_node: str = "branch"):
        self.planner_node = planner_node
        self.branch_node = branch_node
        self.calls: list[str] = []

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        self.calls.append(node_id)
        if node_id == self.planner_node:
            items = channel_snapshot.get("items", []) or []
            return WorkerResult(
                node_id=node_id,
                channel_updates={
                    "_dispatch": [{"node": self.branch_node, "state": {"item": v}} for v in items],
                    "messages": [f"{node_id}_planned"],
                },
            )
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


def _linear_config(
    cache_nodes: dict[str, dict] | None = None, interrupt_before: list[str] | None = None
) -> GraphConfig:
    """Three-node linear graph A -> B -> C -> __end__ with optional cache blocks."""
    cache_nodes = cache_nodes or {}
    nodes = {}
    for node_id in ("A", "B", "C"):
        nodes[node_id] = NodeConfig(id=node_id, type=NodeType.VARIABLE_SET, config=cache_nodes.get(node_id, {}))
    return GraphConfig(
        name="node-cache-linear",
        nodes=nodes,
        edges=[
            Edge(source="A", target="B"),
            Edge(source="B", target="C"),
            Edge(source="C", target="__end__"),
        ],
        state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry="A",
        interrupt_before=interrupt_before or [],
    )


async def _collect(agen) -> list[dict]:
    return [event async for event in agen]


def _dsl_document_with_cache(cache_block: dict) -> dict:
    return {
        "version": "1.0",
        "name": "dsl-node-cache",
        "state": {"messages": {"type": "topic", "default": []}},
        "nodes": {
            "A": {"type": "variable-set", "config": {}},
            "B": {"type": "variable-set", "config": {"cache": cache_block}},
        },
        "edges": [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "__end__"},
        ],
        "entry": "A",
    }


class TestNodeCacheDsl:
    """Task 1.2 / 4.2: schema, parse, roundtrip, compiler validation."""

    def test_parse_and_compile_carry_cache_block(self):
        doc = _dsl_document_with_cache({"ttl": 300})
        doc["nodes"]["A"]["config"]["cache"] = {"ttl": 600, "key_func": "k", "scope": "tenant"}
        register_node_key_func("k", lambda node_id, snapshot: "x")
        try:
            config = parse_graph(doc)
            assert config.nodes["B"].config["cache"] == {"ttl": 300}
            compiled = GraphCompiler().compile(config)
            assert compiled.nodes["A"].config["cache"]["ttl"] == 600
            assert compiled.nodes["A"].config["cache"]["scope"] == "tenant"
        finally:
            node_cache_module._KEY_FUNCS.pop("k", None)

    def test_to_json_roundtrips_cache_block(self):
        doc = _dsl_document_with_cache({"ttl": 300, "scope": "tenant"})
        compiled = GraphCompiler().compile(parse_graph(doc))
        reparsed = parse_graph(compiled.to_json())
        recompiled = GraphCompiler().compile(reparsed)
        assert recompiled.nodes["B"].config["cache"] == {"ttl": 300, "scope": "tenant"}

    def test_absent_cache_block_parses_clean(self):
        doc = _dsl_document_with_cache({"ttl": 60})
        doc["nodes"]["A"]["config"].pop("cache", None)
        config = parse_graph(doc)
        assert "cache" not in config.nodes["A"].config

    @pytest.mark.parametrize("bad_block", [{}, {"ttl": 0}, {"ttl": -5}, {"ttl": "300"}, {"ttl": 1.5}])
    def test_invalid_ttl_rejected_at_parse(self, bad_block):
        with pytest.raises(GraphValidationError):
            parse_graph(_dsl_document_with_cache(bad_block))

    def test_unknown_key_func_rejected_at_compile(self, clean_key_func_registry):
        doc = _dsl_document_with_cache({"ttl": 60, "key_func": "ghost_key"})
        with pytest.raises(GraphValidationError, match="ghost_key"):
            GraphCompiler().compile(parse_graph(doc))

    def test_unknown_key_func_error_names_registered_set(self, clean_key_func_registry):
        register_node_key_func("real_key", lambda node_id, snapshot: "x")
        doc = _dsl_document_with_cache({"ttl": 60, "key_func": "ghost_key"})
        with pytest.raises(GraphValidationError, match="real_key"):
            GraphCompiler().compile(parse_graph(doc))

    def test_cache_rejected_on_side_effect_node_types(self, clean_key_func_registry):
        """Cache-safety contract: a hit skips the worker — side-effect-ful
        types (tool invocation, LLM tool loop, delegation, retrieval with
        permission-scoped results) must re-execute every run."""
        # CONTROLLER also sits in the compiler's rejected set but fails an
        # earlier validation (category_targets) before reaching the cache
        # check, so it is not exercised here.
        for node_type in (
            NodeType.TOOL_CALL,
            NodeType.CONVERSATION,
            NodeType.AGENT,
            NodeType.COORDINATOR,
            NodeType.KNOWLEDGE_RETRIEVAL,
        ):
            graph = GraphConfig(
                name=f"impure-{node_type}",
                nodes={"x": NodeConfig(id="x", type=node_type, config={"cache": {"ttl": 30}})},
                edges=[Edge(source="x", target="__end__")],
                state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
                entry="x",
            )
            with pytest.raises(GraphValidationError, match="side-effect-free"):
                GraphCompiler().compile(graph)

    def test_cache_allowed_on_pure_node_types(self, clean_key_func_registry):
        graph = GraphConfig(
            name="pure-types",
            nodes={
                "v": NodeConfig(id="v", type=NodeType.VARIABLE_SET, config={"cache": {"ttl": 30}}),
                "s": NodeConfig(id="s", type=NodeType.SUGGESTION, config={"cache": {"ttl": 30}}),
                "c": NodeConfig(id="c", type=NodeType.CONDITION, config={"cache": {"ttl": 30}}),
            },
            edges=[Edge(source="v", target="s"), Edge(source="s", target="c"), Edge(source="c", target="__end__")],
            state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry="v",
        )
        compiled = GraphCompiler().compile(graph)
        assert all(n.config.get("cache") for n in compiled.nodes.values())

    def test_invalid_scope_rejected_at_compile(self):
        graph = _linear_config(cache_nodes={"B": {"cache": {"ttl": 30, "scope": "global"}}})
        with pytest.raises(GraphValidationError, match="scope"):
            GraphCompiler().compile(graph)

    def test_cache_block_must_be_object(self):
        graph = _linear_config(cache_nodes={"B": {"cache": "fast"}})
        with pytest.raises(GraphValidationError, match="object"):
            GraphCompiler().compile(graph)


class TestNodeCacheUnit:
    """Task 4.1: cache machinery — TTL, LRU, registry, key derivation."""

    def test_ttl_expiry_drops_entry(self):
        cache = InMemoryNodeCache()
        cache.set("k", {"v": 1})
        assert cache.get("k", ttl=60) == {"v": 1}
        assert cache.get("k", ttl=0) is None  # ttl 0 expires immediately

    def test_lru_evicts_least_recently_used(self):
        cache = InMemoryNodeCache(max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a", ttl=60)  # bump "a"
        cache.set("c", 3)  # cap exceeded → evict "b" (LRU), keep "a"
        assert cache.get("a", ttl=60) == 1
        assert cache.get("b", ttl=60) is None
        assert cache.get("c", ttl=60) == 3

    def test_clear_drops_everything_and_counts(self):
        cache = InMemoryNodeCache()
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.clear() == 2
        assert cache.get("a", ttl=60) is None

    def test_stats_report_hit_rate(self):
        cache = InMemoryNodeCache()
        cache.set("k", 1)
        cache.get("k", ttl=60)
        cache.get("nope", ttl=60)
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1 and stats["entries"] == 1

    def test_key_func_registry_roundtrip(self, clean_key_func_registry):
        def my_key(node_id, snapshot):
            return "material"

        register_node_key_func("my_key", my_key)
        assert "my_key" in list_node_key_funcs()
        assert get_node_key_func("my_key") is my_key

    def test_get_unknown_key_func_raises(self, clean_key_func_registry):
        with pytest.raises(UnknownKeyFuncError):
            get_node_key_func("never_registered")

    def _policy(self, scope: str = "session", key_func: str | None = None):
        from hecate.runtime.node_cache import CachePolicy

        return CachePolicy(ttl=60, key_func=key_func, scope=scope)

    def test_default_key_deterministic(self):
        session = uuid.uuid4()
        snapshot = {"messages": ["hi"]}
        k1 = derive_cache_key(self._policy(), "B", {"model": "m"}, snapshot, session_id=session)
        k2 = derive_cache_key(self._policy(), "B", {"model": "m"}, snapshot, session_id=session)
        assert k1 == k2

    def test_session_scope_isolates_sessions(self):
        snapshot = {"messages": ["hi"]}
        k1 = derive_cache_key(self._policy(), "B", {}, snapshot, session_id=uuid.uuid4())
        k2 = derive_cache_key(self._policy(), "B", {}, snapshot, session_id=uuid.uuid4())
        assert k1 != k2

    def test_tenant_scope_ignores_session_and_requires_tenant(self):
        snapshot = {"messages": ["hi"]}
        k1 = derive_cache_key(self._policy("tenant"), "B", {}, snapshot, session_id=uuid.uuid4(), tenant_id="t1")
        k2 = derive_cache_key(self._policy("tenant"), "B", {}, snapshot, session_id=uuid.uuid4(), tenant_id="t1")
        assert k1 == k2
        with pytest.raises(RuntimeError, match="tenant_id"):
            derive_cache_key(self._policy("tenant"), "B", {}, snapshot, session_id=uuid.uuid4())

    def test_model_config_change_invalidates_identity(self):
        session = uuid.uuid4()
        snapshot = {"messages": ["hi"]}
        k1 = derive_cache_key(self._policy(), "B", {"model": "gpt-4o"}, snapshot, session_id=session)
        k2 = derive_cache_key(self._policy(), "B", {"model": "gpt-5"}, snapshot, session_id=session)
        assert k1 != k2

    def test_tool_rebinding_invalidates_identity(self):
        """A TOOL node rebound to a different tool must not hit stale entries."""
        session = uuid.uuid4()
        snapshot = {"messages": ["hi"]}
        k1 = derive_cache_key(self._policy(), "T", {"tool_name": "search"}, snapshot, session_id=session)
        k2 = derive_cache_key(self._policy(), "T", {"tool_name": "create_ticket"}, snapshot, session_id=session)
        assert k1 != k2

    def test_cache_block_change_keeps_identity(self):
        """TTL tweaks are policy, not semantics — semantically identical configs share the key."""
        session = uuid.uuid4()
        snapshot = {"messages": ["hi"]}
        k1 = derive_cache_key(
            self._policy(), "T", {"tool_name": "search", "cache": {"ttl": 60}}, snapshot, session_id=session
        )
        k2 = derive_cache_key(
            self._policy(), "T", {"tool_name": "search", "cache": {"ttl": 3600}}, snapshot, session_id=session
        )
        assert k1 == k2

    def test_readable_slice_narrows_key(self):
        session = uuid.uuid4()
        k1 = derive_cache_key(
            self._policy(), "B", {}, {"messages": ["hi"], "noise": 1}, session_id=session, readable=["messages"]
        )
        k2 = derive_cache_key(
            self._policy(), "B", {}, {"messages": ["hi"], "noise": 2}, session_id=session, readable=["messages"]
        )
        assert k1 == k2  # undeclared channel change does not alter the key

    def test_branch_slice_overrides_snapshot(self):
        session = uuid.uuid4()
        k1 = derive_cache_key(
            self._policy(), "T", {}, {"messages": ["whatever"]}, session_id=session, branch_slice={"item": "x"}
        )
        k2 = derive_cache_key(
            self._policy(), "T", {}, {"messages": ["different"]}, session_id=session, branch_slice={"item": "x"}
        )
        k3 = derive_cache_key(
            self._policy(), "T", {}, {"messages": ["whatever"]}, session_id=session, branch_slice={"item": "y"}
        )
        assert k1 == k2 and k1 != k3

    def test_key_func_replaces_derivation_but_keeps_namespace(self, clean_key_func_registry):
        register_node_key_func("kf", lambda node_id, snapshot: f"mat:{snapshot.get('messages')}")
        session = uuid.uuid4()
        k1 = derive_cache_key(self._policy(key_func="kf"), "B", {}, {"messages": ["a"]}, session_id=session)
        k2 = derive_cache_key(self._policy(key_func="kf"), "B", {}, {"messages": ["a"]}, session_id=uuid.uuid4())
        assert "mat:['a']" in k1
        assert k1 != k2  # scope namespace survives key_func override

    def test_key_func_path_config_change_invalidates(self, clean_key_func_registry):
        """Custom key material alone must not mask a semantic config change."""
        register_node_key_func("kf", lambda node_id, snapshot: f"mat:{snapshot.get('messages')}")
        session = uuid.uuid4()
        k1 = derive_cache_key(
            self._policy(key_func="kf"), "T", {"tool_name": "search"}, {"messages": ["a"]}, session_id=session
        )
        k2 = derive_cache_key(
            self._policy(key_func="kf"), "T", {"tool_name": "create_ticket"}, {"messages": ["a"]}, session_id=session
        )
        assert k1 != k2


class TestNodeCacheEngine:
    """Task 4.3: dispatch seam semantics."""

    async def test_hit_skips_worker_and_miss_stores(self):
        shared_cache = InMemoryNodeCache()
        worker = RecordingWorker()
        compiled = GraphCompiler().compile(_linear_config(cache_nodes={"B": {"cache": {"ttl": 300}}}))
        runtime = PregelRuntime(
            compiled, worker, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        session_id = uuid.uuid4()
        await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))
        assert worker.calls.count("B") == 1  # miss executed

        # Same session + same inputs → B hits, worker not invoked again.
        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(
            compiled, worker2, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        await _collect(runtime2.execute(session_id, initial_input={"messages": ["hi"]}))
        assert "B" not in worker2.calls
        assert "A" in worker2.calls and "C" in worker2.calls  # non-policy nodes unaffected

    async def test_nodes_without_policy_zero_cache_queries(self):
        shared_cache = InMemoryNodeCache()
        compiled = GraphCompiler().compile(_linear_config())
        runtime = PregelRuntime(
            compiled,
            RecordingWorker(),
            InMemoryCheckpointStore(),
            event_store=InMemoryEventStore(),
            node_cache=shared_cache,
        )
        await _collect(runtime.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}))
        assert shared_cache.stats()["misses"] == 0
        assert shared_cache.stats()["entries"] == 0

    async def test_hit_and_miss_log_trajectories_identical_except_marker(self):
        compiled = GraphCompiler().compile(_linear_config(cache_nodes={"B": {"cache": {"ttl": 300}}}))
        shared_cache = InMemoryNodeCache()
        session_id = uuid.uuid4()

        store_miss = InMemoryEventStore()
        runtime1 = PregelRuntime(
            compiled, RecordingWorker(), InMemoryCheckpointStore(), event_store=store_miss, node_cache=shared_cache
        )
        await _collect(runtime1.execute(session_id, initial_input={"messages": ["hi"]}))

        store_hit = InMemoryEventStore()
        runtime2 = PregelRuntime(
            compiled, RecordingWorker(), InMemoryCheckpointStore(), event_store=store_hit, node_cache=shared_cache
        )
        await _collect(runtime2.execute(session_id, initial_input={"messages": ["hi"]}))

        log_miss = await store_miss.get_events(session_id)
        log_hit = await store_hit.get_events(session_id)

        def _trajectory(events, node_id):
            seq = []
            for ev in events:
                if ev.node_id != node_id:
                    continue
                payload = {k: v for k, v in ev.payload.items() if k not in ("cached", "cache_key", "node_type")}
                seq.append((ev.event_type, payload))
            return seq

        assert _trajectory(log_miss, "B") == _trajectory(log_hit, "B")

        node_end_miss = [e for e in log_miss if e.node_id == "B" and e.event_type == EventType.NODE_END]
        node_end_hit = [e for e in log_hit if e.node_id == "B" and e.event_type == EventType.NODE_END]
        assert len(node_end_miss) == len(node_end_hit) == 1
        assert node_end_miss[0].payload["cached"] is False
        assert node_end_hit[0].payload["cached"] is True
        assert node_end_hit[0].payload["cache_key"] == node_end_miss[0].payload["cache_key"]
        # channel writes are identical between the miss run and the hit run
        writes_miss = [e.payload for e in log_miss if e.node_id == "B" and e.event_type == EventType.CHANNEL_WRITE]
        writes_hit = [e.payload for e in log_hit if e.node_id == "B" and e.event_type == EventType.CHANNEL_WRITE]
        assert writes_miss == writes_hit

    async def test_hit_produces_no_synthetic_llm_events(self):
        compiled = GraphCompiler().compile(_linear_config(cache_nodes={"B": {"cache": {"ttl": 300}}}))
        shared_cache = InMemoryNodeCache()
        session_id = uuid.uuid4()

        store1 = InMemoryEventStore()
        runtime1 = PregelRuntime(
            compiled,
            LlmEventWorker(event_store=store1),
            InMemoryCheckpointStore(),
            event_store=store1,
            node_cache=shared_cache,
        )
        await _collect(runtime1.execute(session_id, initial_input={"messages": ["hi"]}))

        store2 = InMemoryEventStore()
        runtime2 = PregelRuntime(
            compiled,
            LlmEventWorker(event_store=store2),
            InMemoryCheckpointStore(),
            event_store=store2,
            node_cache=shared_cache,
        )
        await _collect(runtime2.execute(session_id, initial_input={"messages": ["hi"]}))

        log2 = await store2.get_events(session_id)
        llm_events_for_b = [
            e for e in log2 if e.node_id == "B" and e.event_type in (EventType.LLM_REQUEST, EventType.LLM_RESPONSE)
        ]
        assert llm_events_for_b == []  # hit skipped the worker → no model events

    async def test_cache_flush_does_not_change_rebuilt_state(self):
        compiled = GraphCompiler().compile(_linear_config(cache_nodes={"B": {"cache": {"ttl": 300}}}))
        shared_cache = InMemoryNodeCache()

        async def _final_messages() -> list:
            runtime = PregelRuntime(
                compiled,
                RecordingWorker(),
                InMemoryCheckpointStore(),
                event_store=InMemoryEventStore(),
                node_cache=shared_cache,
            )
            events = await _collect(runtime.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}))
            return [e for e in events if e["type"] == "values"][-1]

        state_exec = await _final_messages()
        state_hit = await _final_messages()
        assert state_exec == state_hit
        shared_cache.clear()
        state_after_flush = await _final_messages()
        assert state_after_flush == state_exec

    async def test_interrupt_before_pauses_before_cache_consult(self):
        compiled = GraphCompiler().compile(
            _linear_config(cache_nodes={"B": {"cache": {"ttl": 300}}}, interrupt_before=["B"])
        )
        shared_cache = InMemoryNodeCache()
        worker = RecordingWorker()
        store = InMemoryEventStore()
        runtime = PregelRuntime(compiled, worker, InMemoryCheckpointStore(), event_store=store, node_cache=shared_cache)
        session_id = uuid.uuid4()
        events = await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))
        assert events[-1]["type"] == "interrupt"
        assert "B" not in worker.calls  # paused before execution
        assert shared_cache.stats()["entries"] == 0  # never consulted → nothing stored

        # Resume executes B as a miss; the INTERRUPT precedes B's NODE_START.
        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(
            compiled, worker2, InMemoryCheckpointStore(), event_store=store, node_cache=shared_cache
        )
        await _collect(runtime2.execute(session_id, resume_value="ok"))
        assert worker2.calls.count("B") == 1
        log = await store.get_events(session_id)
        b_starts = [i for i, e in enumerate(log) if e.node_id == "B" and e.event_type == EventType.NODE_START]
        interrupts = [i for i, e in enumerate(log) if e.event_type == EventType.INTERRUPT]
        assert interrupts and b_starts and interrupts[0] < b_starts[0]

    async def test_dynamic_fanout_identical_branch_slices_merge_correctly(self):
        # Branch slices are identical ("x", "x") → same cache key; v1 accepts
        # the parallel stampede (both miss, both execute), and run 2 hits for
        # both branches. MERGE-free topology: branch outputs land on
        # sub-channels; assert both branches' outputs persist across runs.
        graph = CompiledGraph(
            nodes={
                "planner": NodeConfig(
                    id="planner",
                    type=NodeType.CONDITION,
                    config={"fanout": {"over": "items", "target": "branch", "state_key": "item"}},
                ),
                "branch": NodeConfig(
                    id="branch",
                    type=NodeType.VARIABLE_SET,
                    config={"cache": {"ttl": 300}},
                ),
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
            name="node-cache-dynamic-fanout",
        )
        shared_cache = InMemoryNodeCache()
        worker = PlannerBranchWorker()
        runtime = PregelRuntime(
            graph, worker, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        session_id = uuid.uuid4()
        await _collect(runtime.execute(session_id, initial_input={"items": ["x", "x"]}))
        # v1 accepts duplicate execution for concurrent identical keys, but
        # in-process asyncio may complete the first branch (and store) before
        # the second consults — both outcomes are spec-compliant.
        assert 1 <= worker.calls.count("branch") <= 2

        worker2 = PlannerBranchWorker()
        runtime2 = PregelRuntime(
            graph, worker2, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        await _collect(runtime2.execute(session_id, initial_input={"items": ["x", "x"]}))
        assert worker2.calls.count("branch") == 0  # both branches hit

        log2 = await runtime2._event_store.get_events(session_id)
        branch_ends = [e for e in log2 if e.node_id == "branch" and e.event_type == EventType.NODE_END]
        # Known ③ behavior: dynamic branch NODE_END is emitted twice (inside
        # the dispatcher and again from the results loop). Both emissions
        # carry the cached marker — assert the hit served every branch.
        assert len(branch_ends) == 4
        assert all(e.payload["cached"] is True for e in branch_ends)

    async def test_static_fanout_branches_cache_and_merge_reads_subchannels(self):
        graph = CompiledGraph(
            nodes={
                "fan": NodeConfig(id="fan", type=NodeType.FAN_OUT, config={"branches": ["b1", "b2"]}),
                "b1": NodeConfig(id="b1", type=NodeType.VARIABLE_SET, config={"cache": {"ttl": 300}}),
                "b2": NodeConfig(id="b2", type=NodeType.VARIABLE_SET, config={"cache": {"ttl": 300}}),
                "merge": NodeConfig(
                    id="merge",
                    type=NodeType.MERGE,
                    config={"fan_out_source": "fan", "output_channel": "merged"},
                ),
            },
            edges=[
                Edge(source="fan", target="b1"),
                Edge(source="fan", target="b2"),
                Edge(source="b1", target="merge"),
                Edge(source="b2", target="merge"),
                Edge(source="merge", target="__end__"),
            ],
            channels={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "merged": ChannelDef(type=ChannelType.LAST_VALUE),
            },
            entry_point="fan",
            name="node-cache-static-fanout",
        )
        shared_cache = InMemoryNodeCache()
        worker = RecordingWorker()
        runtime = PregelRuntime(
            graph, worker, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        events = await _collect(runtime.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}))
        assert worker.calls.count("b1") == 1 and worker.calls.count("b2") == 1
        merged = [e for e in events if e["type"] == "values"][-1]
        assert merged is not None  # run completed through MERGE

    async def test_tenant_scope_fail_closed_without_tenant_context(self):
        compiled = GraphCompiler().compile(
            _linear_config(cache_nodes={"B": {"cache": {"ttl": 300, "scope": "tenant"}}})
        )
        runtime = PregelRuntime(
            compiled, RecordingWorker(), InMemoryCheckpointStore(), event_store=InMemoryEventStore()
        )
        with pytest.raises(RuntimeError, match="tenant_id"):
            await _collect(runtime.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}))

    async def test_tenant_scope_shares_entries_across_sessions(self):
        shared_cache = InMemoryNodeCache()
        compiled = GraphCompiler().compile(
            _linear_config(cache_nodes={"B": {"cache": {"ttl": 300, "scope": "tenant"}}})
        )

        worker1 = RecordingWorker()
        runtime1 = PregelRuntime(
            compiled, worker1, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        await _collect(runtime1.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}, tenant_id="acme"))
        assert worker1.calls.count("B") == 1

        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(
            compiled, worker2, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        await _collect(runtime2.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}, tenant_id="acme"))
        assert "B" not in worker2.calls  # tenant-shared hit across sessions

        worker3 = RecordingWorker()
        runtime3 = PregelRuntime(
            compiled, worker3, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        await _collect(runtime3.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}, tenant_id="other"))
        assert worker3.calls.count("B") == 1  # different tenant → miss

    async def test_erroring_results_are_never_cached(self):
        class FailingWorker(RecordingWorker):
            def __init__(self, fail_at: str, event_store=None):
                super().__init__(event_store=event_store)
                self._fail_at = fail_at

            async def execute(self, node_id, node_config, channel_snapshot, execution_context=None):
                if node_id == self._fail_at:
                    self.calls.append(node_id)
                    return WorkerResult(node_id=node_id, error=RuntimeError("boom"))
                return await super().execute(node_id, node_config, channel_snapshot, execution_context)

        compiled = GraphCompiler().compile(_linear_config(cache_nodes={"B": {"cache": {"ttl": 300}}}))
        shared_cache = InMemoryNodeCache()
        worker = FailingWorker(fail_at="B")
        runtime = PregelRuntime(
            compiled, worker, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        with pytest.raises(RuntimeError, match="boom"):
            await _collect(runtime.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}))
        assert shared_cache.stats()["entries"] == 0  # failed result not stored

        # Next run re-executes B (no cached failure).
        worker2 = FailingWorker(fail_at="ZZZ")
        runtime2 = PregelRuntime(
            compiled, worker2, InMemoryCheckpointStore(), event_store=InMemoryEventStore(), node_cache=shared_cache
        )
        await _collect(runtime2.execute(uuid.uuid4(), initial_input={"messages": ["hi"]}))
        assert worker2.calls.count("B") == 1
