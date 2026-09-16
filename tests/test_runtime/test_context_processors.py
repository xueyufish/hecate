"""Tests for the 4.13 context processor chain.

Covers: atomic unit grouping (unitize), token estimators, each first-party
processor, the chain executor semantics (declaration order + early stop +
aggregation), the executor-owned failure policy, and the legacy-equivalence
golden path (default chain vs the pre-chain pipeline behavior).
"""

from __future__ import annotations

import pytest

from hecate.runtime.context import InMemoryContextEngine
from hecate.runtime.context_processors import (
    AnchorTokenEstimator,
    BudgetWarnProcessor,
    ChainContext,
    CompressionProcessor,
    ContextProcessorChain,
    FailurePolicy,
    HeuristicTokenEstimator,
    HintProcessor,
    KVCacheAwareProcessor,
    OffloadProcessor,
    RoundWindowProcessor,
    TerminationProcessor,
    ToolResultTruncationProcessor,
    cache_hit_rate_from_usage,
    default_chain_processors,
    flatten_units,
    resolve_budget,
    unitize,
)


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _tool_group(call_id: str = "c1", result: str = "r") -> list[dict]:
    return [
        {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": call_id, "content": result},
    ]


class TestUnitize:
    def test_plain_messages_one_unit_each(self) -> None:
        units = unitize([_user("a"), {"role": "assistant", "content": "b"}])
        assert [u.kind for u in units] == ["user", "assistant"]

    def test_tool_group_is_atomic(self) -> None:
        units = unitize([_user("q"), *_tool_group("c1"), {"role": "assistant", "content": "done"}])
        assert [u.kind for u in units] == ["user", "tool_group", "assistant"]
        assert len(units[1].messages) == 2

    def test_dangling_tool_result_dropped(self) -> None:
        units = unitize([_user("q"), {"role": "tool", "tool_call_id": "zz", "content": "orphan"}])
        assert [u.kind for u in units] == ["user"]

    def test_unanswered_call_gets_synthetic_result(self) -> None:
        call_only = [
            {"role": "assistant", "tool_calls": [{"id": "c9", "function": {"name": "f", "arguments": "{}"}}]},
        ]
        units = unitize([_user("q"), *call_only])
        group = units[1]
        assert group.kind == "tool_group"
        assert group.messages[1]["tool_call_id"] == "c9"
        assert "synthetic" in group.messages[1]["content"]

    async def test_never_splits_across_boundary(self) -> None:
        """A window that cuts inside a tool group must keep the group whole."""
        units = unitize([_user("q"), *_tool_group("c1"), {"role": "assistant", "content": "done"}])
        processor = RoundWindowProcessor(ranking="recency")
        ctx = ChainContext(budget=4, estimator=HeuristicTokenEstimator(), state={})
        out, _ = await processor.process(units, ctx)
        groups = [u for u in out if u.kind == "tool_group"]
        # The tool group is either fully kept or fully dropped — never split.
        assert len(groups) in (0, 1)
        if groups:
            assert len(groups[0].messages) == 2


class TestEstimators:
    def test_heuristic(self) -> None:
        est = HeuristicTokenEstimator()
        assert est.estimate_messages([_user("x" * 400)]) == 100
        assert est.estimate_messages([]) == 0

    def test_anchor_covers_prefix(self) -> None:
        est = AnchorTokenEstimator(anchor_prompt_tokens=1000, anchor_chars=800)
        prefix = [_user("x" * 800)]
        assert est.estimate_messages(prefix) == 1000
        grown = [*prefix, _user("y" * 40)]
        assert est.estimate_messages(grown) == 1010

    def test_resolve_budget_priority(self) -> None:
        assert resolve_budget({"max_tokens": 16000}, {"context_budget": 8000}) == 16000
        assert resolve_budget({}, {"context_budget": 12000}) == 12000
        assert resolve_budget({}, {"context_budget_model_default": 25000}) == 25000
        assert resolve_budget({}, {}) == 8000


class TestToolResultTruncation:
    async def test_oversized_result_capped(self) -> None:
        processor = ToolResultTruncationProcessor()
        units = unitize([_user("q"), *_tool_group("c1", result="x" * 9000)])
        ctx = ChainContext(budget=100_000, estimator=HeuristicTokenEstimator(), tool_result_limit=2000)
        out, result = await processor.process(units, ctx)
        assert result.level is None  # normalization, not a degradation level
        flat = flatten_units(out)
        tool_msg = next(m for m in flat if m.get("role") == "tool")
        assert len(tool_msg["content"]) < 9000
        assert "truncated" in tool_msg["content"]

    async def test_small_result_untouched(self) -> None:
        processor = ToolResultTruncationProcessor()
        units = unitize([*_tool_group("c1", result="small")])
        ctx = ChainContext(budget=100_000, estimator=HeuristicTokenEstimator(), tool_result_limit=2000)
        out, _ = await processor.process(units, ctx)
        assert out[0].messages[1]["content"] == "small"


class TestBudgetWarn:
    async def test_injects_once_per_crossing(self) -> None:
        state: dict = {}
        processor = BudgetWarnProcessor(threshold=0.8)
        chain = ContextProcessorChain([processor], session_state=state)
        units = unitize([_user("x" * 1000)])
        ctx = ChainContext(
            budget=100,
            estimator=HeuristicTokenEstimator(),
            session_id="s1",
            state=chain.session_state,
        )
        # Cross the threshold: hint injected (appended user message).
        out, result = await processor.process(units, ctx)
        assert result.level == "warn"
        assert out[-1].messages[0]["role"] == "user"
        assert "budget_warning" in out[-1].messages[0]["content"]
        # Still above threshold: latched, no second hint.
        out2, result2 = await processor.process(units, ctx)
        assert result2.level is None
        assert result2.metadata.get("latched") is True
        assert len(out2) == len(units)
        # Below threshold resets the latch; a new crossing warns again.
        small = unitize([_user("x")])
        ctx_small = ChainContext(
            budget=100,
            estimator=HeuristicTokenEstimator(),
            session_id="s1",
            state=chain.session_state,
        )
        await processor.process(small, ctx_small)
        out3, result3 = await processor.process(units, ctx_small)
        assert result3.level == "warn"

    async def test_below_threshold_no_hint(self) -> None:
        processor = BudgetWarnProcessor(threshold=0.8)
        units = unitize([_user("x" * 40)])
        ctx = ChainContext(budget=1000, estimator=HeuristicTokenEstimator(), session_id="s1", state={})
        out, result = await processor.process(units, ctx)
        assert result.level is None
        assert len(out) == len(units)

    async def test_system_prompt_untouched(self) -> None:
        processor = BudgetWarnProcessor(threshold=0.5)
        units = unitize([{"role": "system", "content": "sys"}, _user("x" * 900)])
        ctx = ChainContext(budget=100, estimator=HeuristicTokenEstimator(), session_id="s1", state={})
        out, _ = await processor.process(units, ctx)
        assert out[0].messages[0]["content"] == "sys"


class TestRoundWindow:
    async def test_engine_path_suffix_equivalence(self) -> None:
        engine = InMemoryContextEngine()
        messages = [_user("x" * 200) for _ in range(20)]
        processor = RoundWindowProcessor()
        ctx = ChainContext(budget=100, estimator=HeuristicTokenEstimator(), engine=engine, state={})
        units = unitize(messages)
        out, result = await processor.process(units, ctx)
        flat = flatten_units(out)
        assert flat == messages[-len(flat) :]
        assert result.level == "drop"

    async def test_recency_window_pins_system_and_newest_user(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            _user("x" * 400),
            _user("y" * 400),
            _user("keep"),
        ]
        processor = RoundWindowProcessor(ranking="recency")
        ctx = ChainContext(budget=120, estimator=HeuristicTokenEstimator(), state={})
        out, result = await processor.process(unitize(messages), ctx)
        flat = flatten_units(out)
        roles_contents = [m["content"] for m in flat]
        assert "sys" in roles_contents
        assert "keep" in roles_contents
        assert result.level == "drop"

    async def test_protected_prefix_never_dropped_until_yielded(self) -> None:
        messages = [_user(f"m{i}" * 100) for i in range(10)]
        units = unitize(messages)
        processor = RoundWindowProcessor(ranking="recency")
        ctx = ChainContext(budget=400, estimator=HeuristicTokenEstimator(), state={"protected_upto": 8})
        out, result = await processor.process(units, ctx)
        flat = flatten_units(out)
        # Protected units (0..7) survive unless protection was yielded.
        if not result.metadata.get("prefix_protection_yielded"):
            for i in range(8):
                assert messages[i] in flat


class TestOffload:
    async def test_offload_replaces_dropped_block_with_stub(self) -> None:
        class _StubOffloader:
            threshold_tokens = 10

            def __init__(self) -> None:
                self.offloaded: list | None = None

            def is_enabled(self) -> bool:
                return True

            async def offload(self, messages, session_id):
                self.offloaded = messages
                return {"role": "system", "content": f"[offloaded to memory/sessions/{session_id}/f.json]"}

        offloader = _StubOffloader()
        messages = [_user("x" * 400) for _ in range(5)]
        window = RoundWindowProcessor(ranking="recency")
        offload = OffloadProcessor()
        ctx = ChainContext(
            budget=200,
            estimator=HeuristicTokenEstimator(),
            offloader=offloader,
            session_id="s1",
            state={},
        )
        units = unitize(messages)
        out, wres = await window.process(units, ctx)
        ctx.state["original_units"] = list(units)
        # Simulate the chain: dropped units recorded by the window processor.
        kept_ids = {id(u) for u in out}
        ctx.state["dropped_units"] = [u for u in units if id(u) not in kept_ids]
        out2, ores = await offload.process(out, ctx)
        assert ores.metadata["offloaded"] is True
        assert offloader.offloaded is not None
        stub = out2[0].messages[0]
        assert "offloaded" in stub["content"]

    async def test_no_offloader_is_noop(self) -> None:
        offload = OffloadProcessor()
        units = unitize([_user("q")])
        ctx = ChainContext(budget=10, estimator=HeuristicTokenEstimator(), offloader=None, state={})
        out, result = await offload.process(units, ctx)
        assert result.metadata["reason"] == "no_offloader"


class TestCompression:
    async def test_engine_compression_records_level(self) -> None:
        engine = InMemoryContextEngine(max_messages=3)
        messages = [_user(f"m{i:02d}") for i in range(20)]
        processor = CompressionProcessor()
        ctx = ChainContext(budget=5, estimator=HeuristicTokenEstimator(), engine=engine, state={})
        out, result = await processor.process(unitize(messages), ctx)
        assert result.level == "compress"
        assert len(out) < len(messages)

    async def test_surface_replacement_backend_not_implemented(self) -> None:
        processor = CompressionProcessor(backend="surface_replacement")
        ctx = ChainContext(budget=5, estimator=HeuristicTokenEstimator(), state={})
        with pytest.raises(NotImplementedError):
            await processor.process(unitize([_user("q")]), ctx)


class TestTermination:
    async def test_still_over_budget_sets_stop_reason(self) -> None:
        class _NeverFitsEngine:
            def select_messages(self, history, budget):
                return list(history)

            def compress(self, messages):
                return list(messages)

            def estimate_tokens(self, messages):
                return 10_000 if messages else 0

        messages = [_user("x" * 50) for _ in range(10)]
        chain = ContextProcessorChain(default_chain_processors())
        report = await chain.apply(
            messages,
            {},
            {"context_budget": 10, "context_engine": _NeverFitsEngine(), "session_id": "s1"},
            "n1",
        )
        assert report.stop_reason == "token_capped"
        assert report.levels[-1] == "terminate"


class TestHintProcessor:
    async def test_time_hint_fires_once_then_dedups(self) -> None:
        processor = HintProcessor(time_interval_minutes=60)
        state: dict = {}
        units = unitize([_user("q")])
        ctx = ChainContext(budget=10_000, estimator=HeuristicTokenEstimator(), session_id="s1", state=state)
        out1, r1 = await processor.process(units, ctx)
        assert r1.metadata["hints"] >= 1
        out2, r2 = await processor.process(units, ctx)
        assert r2.metadata["hints"] == 0

    async def test_usage_hint_near_threshold(self) -> None:
        processor = HintProcessor(time_interval_minutes=60, usage_buffer_ratio=0.5)
        units = unitize([_user("x" * 800)])
        ctx = ChainContext(budget=100, estimator=HeuristicTokenEstimator(), session_id="s2", state={})
        out, result = await processor.process(units, ctx)
        joined = " ".join(m["content"] for u in out for m in u.messages)
        assert "Context usage" in joined

    async def test_never_modifies_system_prompt(self) -> None:
        processor = HintProcessor(time_interval_minutes=60)
        units = unitize([{"role": "system", "content": "SYS"}])
        ctx = ChainContext(budget=10_000, estimator=HeuristicTokenEstimator(), session_id="s3", state={})
        out, _ = await processor.process(units, ctx)
        assert out[0].messages[0]["content"] == "SYS"
        assert all(u.messages[0].get("role") != "system" for u in out[1:])


class TestKVCacheAware:
    async def test_sets_protected_prefix_and_annotates_breakpoint(self) -> None:
        messages = [_user(f"m{i}") for i in range(10)]
        units = unitize(messages)
        processor = KVCacheAwareProcessor(window_units=4)
        ctx = ChainContext(budget=10, estimator=HeuristicTokenEstimator(), state={})
        out, result = await processor.process(units, ctx)
        assert ctx.state["protected_upto"] == 6
        annotated = out[6].messages[0]
        assert annotated.get("cache_hint") == "breakpoint"
        assert all("cache_hint" not in u.messages[0] for u in out[:6])

    async def test_head_system_units_always_protected(self) -> None:
        messages = [{"role": "system", "content": "s1"}, *[_user(f"m{i}") for i in range(10)]]
        units = unitize(messages)
        processor = KVCacheAwareProcessor(window_units=10)
        ctx = ChainContext(budget=10, estimator=HeuristicTokenEstimator(), state={})
        await processor.process(units, ctx)
        assert ctx.state["protected_upto"] >= 1


class TestFailurePolicy:
    def test_breaker_opens_after_threshold(self) -> None:
        policy = FailurePolicy(failure_threshold=3)
        for _ in range(3):
            policy.record_failure("compression")
        assert policy.allow("compression") is False

    def test_cooldown_escalates(self) -> None:
        policy = FailurePolicy(failure_threshold=100, cooldown_base_seconds=60.0)
        policy.record_failure("compression")
        first_delay = policy._cooldown_delay["compression"]
        policy.record_failure("compression")
        second_delay = policy._cooldown_delay["compression"]
        assert second_delay > first_delay

    def test_half_open_after_fallback_successes(self) -> None:
        policy = FailurePolicy(failure_threshold=2, half_open_successes=2)
        policy.record_failure("compression")
        policy.record_failure("compression")
        assert policy.allow("compression") is False
        policy.record_fallback_success("compression")
        policy.record_fallback_success("compression")
        assert policy.allow("compression") is True

    def test_anti_thrash(self) -> None:
        policy = FailurePolicy(min_savings_pct=10.0, anti_thrash_min_new_messages=8)
        assert policy.should_anti_thrash("compression", last_savings_pct=5.0, new_messages_since=2) is True
        assert policy.should_anti_thrash("compression", last_savings_pct=50.0, new_messages_since=2) is False
        assert policy.should_anti_thrash("compression", last_savings_pct=5.0, new_messages_since=20) is False
        forced = policy.should_anti_thrash("compression", last_savings_pct=5.0, new_messages_since=2, forced=True)
        assert forced is False

    async def test_chain_skips_open_processor(self) -> None:
        calls: list[str] = []

        class _Flaky(CompressionProcessor):
            async def process(self, units, ctx):
                calls.append("compression")
                raise RuntimeError("summarizer down")

        messages = [_user("x" * 400) for _ in range(6)]
        chain = ContextProcessorChain(
            [RoundWindowProcessor(ranking="recency"), _Flaky()],
            failure_policy=FailurePolicy(failure_threshold=1),
        )
        for _ in range(3):
            await chain.apply(messages, {}, {"context_budget": 50, "session_id": "s1"}, "n1")
        # First apply runs (and fails) the flaky processor; afterwards the
        # breaker blocks it and the fallback counter path is used instead.
        assert len(calls) <= 2


class TestChainExecutor:
    async def test_under_budget_processors_skip(self) -> None:
        fired: list[str] = []

        class _Spy(RoundWindowProcessor):
            async def process(self, units, ctx):
                fired.append(self.name)
                return await super().process(units, ctx)

        chain = ContextProcessorChain([_Spy(), TerminationProcessor()])
        report = await chain.apply([_user("hi")], {}, {"context_budget": 8000, "session_id": "s1"}, "n1")
        assert fired == []
        assert report.levels == []

    async def test_single_snapshot_per_invocation(self) -> None:
        class _Store:
            def __init__(self) -> None:
                self.events = []

            async def append(self, event) -> None:
                self.events.append(event)

        store = _Store()
        messages = [_user("x" * 300) for _ in range(10)]
        chain = ContextProcessorChain(default_chain_processors())
        report = await chain.apply(
            messages,
            {},
            {
                "context_budget": 100,
                "context_engine": InMemoryContextEngine(),
                "event_store": store,
                "session_id": "s1",
            },
            "n1",
        )
        assert report.levels
        snapshots = [e for e in store.events if e.payload.get("event_name") == "BUDGET_SNAPSHOT"]
        assert len(snapshots) == 1

    async def test_legacy_equivalence_golden(self) -> None:
        """Default chain (engine-only ctx) reproduces the pre-chain pipeline
        output byte-for-byte on flat histories with a suffix engine."""
        messages = [{"role": "user", "content": "x" * 200} for _ in range(20)]
        engine = InMemoryContextEngine()
        chain = ContextProcessorChain(default_chain_processors())
        report = await chain.apply(messages, {}, {"context_budget": 100, "context_engine": engine}, "n1")
        # Legacy pipeline: select_messages returns the fitting suffix.
        expected = engine.select_messages(messages, 100)
        assert report.messages == expected


class TestCacheMetrics:
    def test_hit_rate_from_usage(self) -> None:
        assert cache_hit_rate_from_usage({"prompt_tokens": 100, "cached_tokens": 80}) == 0.8
        assert cache_hit_rate_from_usage({"input_tokens": 100, "cache_read_input_tokens": 25}) == 0.25
        assert cache_hit_rate_from_usage({"prompt_tokens": 100}) is None
        assert cache_hit_rate_from_usage(None) is None
