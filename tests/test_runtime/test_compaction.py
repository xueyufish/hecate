"""Tests for durable compaction via surface replacement (ADR-033).

Covers: the shadowing ledger (loading, anchor validation, rolling supersession),
the capacity-axis trigger at the chain entry (independent of the budget ladder),
the bracket event sequence and its lock semantics, the summary QA gate, and the
cold-chain re-derivation that resume relies on.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.compaction import (
    CompactionEntry,
    CompactionSummarizer,
    apply_ledger,
    load_compaction_state,
    message_anchor,
    render_summary_message,
    resolve_context_window,
    surface_token_estimate,
    validate_summary,
)
from hecate.runtime.context_processors import CompressionProcessor, ContextProcessorChain
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.types import ChannelDef, ChannelType, CompiledGraph, Edge, NodeConfig, NodeType


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _system(content: str = "you are an agent") -> dict:
    return {"role": "system", "content": content}


def _big_messages(count: int, size: int = 400) -> list[dict]:
    return [_user(f"m{i:02d}-{'x' * size}") for i in range(count)]


def _summary(**overrides) -> dict:
    node = {
        "objective": "finish the migration",
        "key_decisions": ["use sqlite"],
        "current_state": "mid-task",
        "next_steps": ["run tests"],
        "critical_context": "keep id-42",
    }
    node.update(overrides)
    return node


class StubSummarizer(CompactionSummarizer):
    def __init__(self, summary: dict | None = None, error: Exception | None = None) -> None:
        self.calls: list[list[dict]] = []
        self._summary = summary
        self._error = error

    async def summarize(self, messages: list[dict]) -> dict:
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        return self._summary if self._summary is not None else _summary()


def _execution_context(store: InMemoryEventStore, session_id: str = "s1", window: int = 1000) -> dict:
    return {"event_store": store, "session_id": session_id, "context_window": window}


def _surface_chain(
    store: InMemoryEventStore,
    summarizer: CompactionSummarizer,
    session_id: str = "s1",
    trigger_ratio: float = 0.8,
    retain_ratio: float = 0.16,
    window: int = 1000,
    with_termination: bool = False,
    listener: list | None = None,
) -> ContextProcessorChain:
    processors: list = [
        CompressionProcessor(backend="surface_replacement", trigger_ratio=trigger_ratio, retain_ratio=retain_ratio)
    ]
    if with_termination:
        from hecate.runtime.context_processors import TerminationProcessor

        processors.append(TerminationProcessor())
    return ContextProcessorChain(
        processors,
        compaction_summarizer=summarizer,
        compaction_listener=(lambda sid, cid: listener.append((sid, cid))) if listener is not None else None,
    )


def _events_of_type(store: InMemoryEventStore, session_id: str, *etypes: EventType) -> list[Event]:
    try:
        key = uuid.UUID(session_id)
    except ValueError:
        key = session_id
    return [e for e in store._store.get(key, []) if e.event_type in etypes]


# ---------------------------------------------------------------------------
# Ledger primitives
# ---------------------------------------------------------------------------


class TestLedgerLoading:
    async def test_bracket_events_fold_into_entry(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        await store.append(
            Event(session_id=sid, superstep=0, event_type=EventType.COMPACTION_STARTED, payload={"compaction_id": "c1"})
        )
        await store.append(
            Event(
                session_id=sid,
                superstep=0,
                event_type=EventType.COMPACTION_SUMMARY,
                payload={"compaction_id": "c1", "summary": _summary(), "shadowed_seqs": [0, 3]},
            )
        )
        await store.append(
            Event(
                session_id=sid,
                superstep=0,
                event_type=EventType.CONTEXT_SURFACE_REPLACED,
                payload={"compaction_id": "c1", "start_seq": 0, "end_seq": 3, "start_anchor": "a", "end_anchor": "b"},
            )
        )
        await store.append(
            Event(
                session_id=sid, superstep=0, event_type=EventType.COMPACTION_COMPLETED, payload={"compaction_id": "c1"}
            )
        )

        state = await load_compaction_state(store, str(sid), cache=None)
        assert state.open_bracket is None
        assert len(state.entries) == 1
        assert state.entries[0].summary == _summary()
        assert (state.entries[0].start_seq, state.entries[0].end_seq) == (0, 3)

    async def test_orphan_start_is_lock_until_turn_end(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        await store.append(
            Event(session_id=sid, superstep=0, event_type=EventType.COMPACTION_STARTED, payload={"compaction_id": "c1"})
        )

        state = await load_compaction_state(store, str(sid), cache=None)
        assert state.open_bracket is not None
        assert not state.open_bracket.stale

        await store.append(
            Event(session_id=sid, superstep=0, event_type=EventType.TURN_END, payload={"reason": "graph_complete"})
        )
        state = await load_compaction_state(store, str(sid), cache=None)
        assert state.open_bracket is not None and state.open_bracket.stale

    async def test_failure_marker_resolves_lock(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        await store.append(
            Event(session_id=sid, superstep=0, event_type=EventType.COMPACTION_STARTED, payload={"compaction_id": "c1"})
        )
        await store.append(
            Event(
                session_id=sid,
                superstep=0,
                event_type=EventType.CUSTOM,
                payload={
                    "event_name": "COMPACTION_FAILED",
                    "compaction_id": "c1",
                    "phase": "summarize",
                    "error": "boom",
                },
            )
        )

        state = await load_compaction_state(store, str(sid), cache=None)
        assert state.open_bracket is not None and state.open_bracket.failed

    async def test_incremental_cache_only_fetches_new_events(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        await store.append(
            Event(session_id=sid, superstep=0, event_type=EventType.COMPACTION_STARTED, payload={"compaction_id": "c1"})
        )
        cache: dict = {}
        state = await load_compaction_state(store, str(sid), cache)
        assert state.open_bracket is not None

        await store.append(Event(session_id=sid, superstep=0, event_type=EventType.TURN_END, payload={}))
        state = await load_compaction_state(store, str(sid), cache)
        assert state.open_bracket is not None and state.open_bracket.stale


class TestApplyLedger:
    def _messages(self, count: int = 10) -> list[dict]:
        return [_user(f"m{i}") for i in range(count)]

    def _entry(self, start: int, end: int, messages: list[dict], cid: str = "c1") -> CompactionEntry:
        return CompactionEntry(
            compaction_id=cid,
            start_seq=start,
            end_seq=end,
            start_anchor=message_anchor(messages[start]),
            end_anchor=message_anchor(messages[end]),
            summary=_summary(),
            prior_compaction_id=None,
            version=1,
        )

    def test_summary_substitutes_range(self) -> None:
        messages = self._messages()
        projection = apply_ledger(messages, [self._entry(2, 5, messages)])
        assert projection.messages[0] == messages[0]
        assert projection.messages[1] == messages[1]
        assert projection.messages[2]["content"].startswith("[context compaction]")
        assert projection.messages[3] == messages[6]
        assert projection.origin[:2] == [0, 1]
        assert projection.origin[2] is None
        assert projection.origin[3] == 6

    def test_anchor_mismatch_fails_open(self) -> None:
        messages = self._messages()
        entry = self._entry(2, 5, messages)
        entry.start_anchor = "stale"
        projection = apply_ledger(messages, [entry])
        assert projection.messages == messages
        assert all(o is not None for o in projection.origin)

    def test_later_range_supersedes_earlier_overlap(self) -> None:
        messages = self._messages()
        first = self._entry(0, 5, messages, cid="c1")
        second = self._entry(3, 9, messages, cid="c2")
        projection = apply_ledger(messages, [first, second])
        # 0-2 covered by the first node, 3-9 by the second node.
        assert projection.messages[0]["content"].startswith("[context compaction]")
        assert projection.messages[1]["content"].startswith("[context compaction]")
        assert projection.shadow_source[0].compaction_id == "c1"
        assert projection.shadow_source[1].compaction_id == "c2"
        assert len(projection.messages) == 2

    def test_entry_without_summary_skipped(self) -> None:
        messages = self._messages()
        entry = self._entry(0, 5, messages)
        entry.summary = None
        projection = apply_ledger(messages, [entry])
        assert projection.messages == messages


class TestSummaryContract:
    def test_validate_rejects_missing_fields(self) -> None:
        assert validate_summary({"objective": "x"}) is not None
        assert validate_summary("text") is not None
        assert validate_summary(_summary()) is None

    def test_validate_rejects_empty_fields(self) -> None:
        assert validate_summary(_summary(key_decisions=[])) is not None
        assert validate_summary(_summary(objective="  ")) is not None

    def test_render_is_user_message(self) -> None:
        message = render_summary_message(_summary())
        assert message["role"] == "user"
        assert "[context compaction]" in message["content"]
        assert "keep id-42" in message["content"]


class TestSurfaceTokenEstimate:
    def test_matches_chars_over_four(self) -> None:
        assert surface_token_estimate([_user("x" * 400)]) == 100
        assert surface_token_estimate([]) == 0


class TestWindowResolution:
    async def test_injected_value_wins(self) -> None:
        window = await resolve_context_window({}, {"context_window": 555})
        assert window == 555

    async def test_provider_lookup_second(self) -> None:
        async def provider(model: str) -> int | None:
            return 12345

        window = await resolve_context_window({"model": "m"}, {}, provider)
        assert window == 12345

    async def test_capability_hints_fallback(self) -> None:
        window = await resolve_context_window({"model": "glm-5"}, {}, None)
        assert window == 200_000

    async def test_unresolvable_is_none(self) -> None:
        assert await resolve_context_window({}, {}, None) is None


# ---------------------------------------------------------------------------
# Chain integration
# ---------------------------------------------------------------------------


class TestSurfaceReplacementChain:
    async def test_bracket_recorded_and_surface_replaced(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, _execution_context(store))

        assert report.metadata["compression"]["compacted"] is True
        assert report.messages[0]["content"].startswith("[context compaction]")
        assert report.messages[0]["role"] == "user"
        # Retained tail keeps the newest messages verbatim.
        assert report.messages[-1] == messages[-1]

        bracket = _events_of_type(
            store,
            "s1",
            EventType.COMPACTION_STARTED,
            EventType.COMPACTION_SUMMARY,
            EventType.CONTEXT_SURFACE_REPLACED,
            EventType.COMPACTION_COMPLETED,
        )
        assert [e.event_type for e in bracket] == [
            EventType.COMPACTION_STARTED,
            EventType.COMPACTION_SUMMARY,
            EventType.CONTEXT_SURFACE_REPLACED,
            EventType.COMPACTION_COMPLETED,
        ]
        replaced = bracket[2].payload
        assert (replaced["start_seq"], replaced["end_seq"]) == (0, 7)
        assert replaced["start_anchor"] == message_anchor(messages[0])
        assert replaced["end_anchor"] == message_anchor(messages[7])
        completed = bracket[3].payload
        assert completed["tokens_after"] < completed["tokens_before"]

    async def test_trigger_fires_even_when_projection_fits_budget(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        # A generous budget leaves the projection untouched by the ladder; the
        # capacity axis must still fire (starvation guard).
        report = await chain.apply(messages, {"max_tokens": 100_000}, _execution_context(store))
        assert report.metadata["compression"]["compacted"] is True

    async def test_below_trigger_line_is_noop(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)

        report = await chain.apply(_big_messages(3), {}, _execution_context(store))
        assert "compression" not in report.metadata
        assert report.messages == _big_messages(3)
        assert _events_of_type(store, "s1", EventType.COMPACTION_STARTED) == []

    async def test_subsequent_invocation_uses_ledger_view(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)
        await chain.apply(messages, {}, _execution_context(store))

        grown = messages + [_user("after-1"), _user("after-2")]
        report = await chain.apply(grown, {}, _execution_context(store))
        assert report.messages[0]["content"].startswith("[context compaction]")
        assert report.messages[-2:] == grown[-2:]
        assert all("m07-" not in (m.get("content") or "") for m in report.messages)

    async def test_cold_chain_rederives_view_from_log(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)
        await chain.apply(messages, {}, _execution_context(store))

        # A fresh chain (empty session state) models post-resume re-derivation.
        cold = _surface_chain(store, summarizer)
        report = await cold.apply(messages, {}, _execution_context(store))
        assert report.messages[0]["content"].startswith("[context compaction]")
        assert report.messages[1:] == messages[8:]

    async def test_ledger_applies_without_surface_backend(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        recording = _surface_chain(store, summarizer)
        messages = _big_messages(10)
        await recording.apply(messages, {}, _execution_context(store))

        plain = ContextProcessorChain([])
        report = await plain.apply(messages, {}, _execution_context(store))
        assert report.messages[0]["content"].startswith("[context compaction]")


class TestSummaryQualityGate:
    async def test_non_shrinking_summary_rejected(self) -> None:
        store = InMemoryEventStore()
        bloated = _summary(critical_context="y" * 8000)
        summarizer = StubSummarizer(summary=bloated)
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, _execution_context(store))
        assert report.metadata["compression"]["compacted"] is False
        assert report.metadata["compression"]["reason"] == "summary_not_shrinking"
        # No replacement took effect; only START and the failure marker exist.
        assert _events_of_type(store, "s1", EventType.CONTEXT_SURFACE_REPLACED) == []
        assert report.messages == messages

    async def test_structurally_invalid_summary_rejected(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer(summary={"objective": "only this"})
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, _execution_context(store))
        assert report.metadata["compression"]["reason"].startswith("summary_invalid")
        assert _events_of_type(store, "s1", EventType.CONTEXT_SURFACE_REPLACED) == []

    async def test_summarizer_exception_is_audited_and_handled_by_policy(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer(error=RuntimeError("provider down"))
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, _execution_context(store))
        assert report.messages == messages
        failed = _events_of_type(store, "s1", EventType.CUSTOM)
        assert failed and failed[0].payload["event_name"] == "COMPACTION_FAILED"
        # The failure policy opened a cooldown: the next invocation skips the
        # summarizer entirely instead of hammering a broken provider.
        assert summarizer.calls and len(summarizer.calls) == 1
        await chain.apply(messages, {}, _execution_context(store))
        assert len(summarizer.calls) == 1


class TestBracketLock:
    async def test_open_bracket_blocks_new_compaction(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        session_id = str(sid)
        await store.append(
            Event(
                session_id=sid,
                superstep=0,
                event_type=EventType.COMPACTION_STARTED,
                payload={"compaction_id": "orphan"},
            )
        )
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, _execution_context(store, session_id=session_id))
        assert report.metadata["compression"]["reason"] == "bracket_open"
        assert summarizer.calls == []

    async def test_stale_bracket_allows_compaction(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        session_id = str(sid)
        await store.append(
            Event(
                session_id=sid,
                superstep=0,
                event_type=EventType.COMPACTION_STARTED,
                payload={"compaction_id": "orphan"},
            )
        )
        await store.append(
            Event(session_id=sid, superstep=0, event_type=EventType.TURN_END, payload={"reason": "graph_complete"})
        )
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, _execution_context(store, session_id=session_id))
        assert report.metadata["compression"]["compacted"] is True

    async def test_in_process_lock_skips_without_waiting(self) -> None:
        from hecate.runtime.compaction import _session_lock

        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        lock = _session_lock("s1")
        await lock.acquire()
        try:
            report = await chain.apply(messages, {}, _execution_context(store))
            assert report.metadata["compression"]["reason"] == "compaction_in_progress"
        finally:
            lock.release()


class TestRollingRecompaction:
    async def test_second_compaction_covers_first_and_chains_ids(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        await chain.apply(messages, {}, _execution_context(store))
        first_entries = _events_of_type(store, "s1", EventType.CONTEXT_SURFACE_REPLACED)
        assert len(first_entries) == 1
        first_cid = first_entries[0].payload["compaction_id"]

        # Grow the conversation until the effective surface crosses the line again.
        grown = messages + _big_messages(8, size=400)
        report = await chain.apply(grown, {}, _execution_context(store))
        assert report.metadata["compression"]["compacted"] is True

        summaries = _events_of_type(store, "s1", EventType.COMPACTION_SUMMARY)
        assert summaries[-1].payload.get("prior_compaction_id") == first_cid
        replaced = _events_of_type(store, "s1", EventType.CONTEXT_SURFACE_REPLACED)
        assert len(replaced) == 2
        # The new range starts where the old one started and covers the tail.
        assert replaced[1].payload["start_seq"] == replaced[0].payload["start_seq"]
        assert replaced[1].payload["end_seq"] > replaced[0].payload["end_seq"]
        # Exactly one summary node in the projection.
        assert sum(1 for m in report.messages if str(m.get("content", "")).startswith("[context compaction]")) == 1

    async def test_anchor_mismatch_fails_open_then_self_heals(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)
        await chain.apply(messages, {}, _execution_context(store))

        # Simulate an ordinal shift: the recorded anchors no longer line up.
        # The stale entry is skipped (fail-open) and the still-armed trigger
        # re-compacts on the current ordinals within the same invocation.
        shifted = [_user("intruder")] + messages
        report = await chain.apply(shifted, {}, _execution_context(store))
        assert report.metadata["compression"]["compacted"] is True
        replaced = _events_of_type(store, "s1", EventType.CONTEXT_SURFACE_REPLACED)
        assert replaced[-1].payload["start_seq"] == 0
        # The healed range anchors on current content: the tail boundary moved
        # one ordinal right after the insertion (m07 now sits at index 8).
        assert replaced[-1].payload["end_seq"] == 8
        assert replaced[-1].payload["end_anchor"] == message_anchor(shifted[8])

        # The healed ledger hides exactly the anchored range, originals stay.
        final = await chain.apply(shifted, {}, _execution_context(store))
        assert final.messages[0]["content"].startswith("[context compaction]")
        assert final.messages[0] is not None
        assert not any(str(m.get("content", "")).startswith("m00-") for m in final.messages[1:])


class TestRetentionBoundary:
    async def test_tail_and_system_never_shadowed(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = [_system("system prompt")] + _big_messages(12)

        report = await chain.apply(messages, {}, _execution_context(store))
        assert report.metadata["compression"]["compacted"] is True
        assert report.messages[0] == messages[0]
        replaced = _events_of_type(store, "s1", EventType.CONTEXT_SURFACE_REPLACED)[-1].payload
        assert replaced["start_seq"] == 1  # the system message stays unshadowed
        assert report.messages[-1] == messages[-1]

    async def test_only_system_units_is_noop(self) -> None:
        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)

        report = await chain.apply([_system("only system"), _system("more sys")], {}, _execution_context(store))
        assert "compression" not in report.metadata
        assert summarizer.calls == []


class TestMissingComponents:
    async def test_no_event_store_is_noop(self) -> None:
        summarizer = StubSummarizer()
        chain = _surface_chain(InMemoryEventStore(), summarizer)
        messages = _big_messages(10)

        report = await chain.apply(messages, {}, {"session_id": "s1", "context_window": 1000})
        assert report.messages == messages
        assert summarizer.calls == []

    async def test_unresolvable_window_fails_fast(self) -> None:
        from hecate.runtime.context_policy import ChainPolicyError

        store = InMemoryEventStore()
        summarizer = StubSummarizer()
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(10)

        with pytest.raises(ChainPolicyError):
            await chain.apply(messages, {}, {"event_store": store, "session_id": "s1"})


class TestSummaryBounding:
    async def test_oversized_summary_fields_bounded_in_payload(self) -> None:
        store = InMemoryEventStore()
        bloated = _summary(critical_context="y" * 50_000)
        summarizer = StubSummarizer(summary=bloated)
        chain = _surface_chain(store, summarizer)
        messages = _big_messages(30)

        report = await chain.apply(messages, {}, _execution_context(store))
        assert report.metadata["compression"]["compacted"] is True
        summaries = _events_of_type(store, "s1", EventType.COMPACTION_SUMMARY)
        stored = summaries[-1].payload["summary"]["critical_context"]
        assert len(stored) < 8_100
        assert "[truncated" in stored


class TestFactoryForwarding:
    def test_factory_forwards_compaction_wiring_to_chains(self) -> None:
        from hecate.runtime.context_policy import ContextChainFactory

        summarizer = StubSummarizer()
        factory = ContextChainFactory(compaction_summarizer=summarizer)
        chain = factory.chain_for_node({})
        assert chain._compaction_summarizer is summarizer
        factory._note_compaction("s9", "c-1")
        assert factory.last_compaction_id("s9") == "c-1"


class TestRuntimeWiring:
    """Assembly-time exclusivity and checkpoint metadata (ADR-033 D4/D10)."""

    def _graph(self, node_processors: list | None = None) -> CompiledGraph:
        config: dict = {"model": "test"}
        if node_processors is not None:
            config["context_processors"] = node_processors
        return CompiledGraph(
            nodes={"A": NodeConfig(id="A", type=NodeType.CONVERSATION, config=config)},
            edges=[Edge(source="A", target="__end__")],
            channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry_point="A",
            name="compaction-wiring",
        )

    def test_runtime_rejects_surface_backend_with_active_eviction(self) -> None:
        from hecate.runtime.context_policy import ChainPolicyError, ContextChainFactory
        from hecate.runtime.eviction import SizeBasedEviction

        with pytest.raises(ChainPolicyError, match="eviction"):
            PregelRuntime(
                self._graph([{"type": "compression", "params": {"backend": "surface_replacement"}}]),
                object(),
                InMemoryCheckpointStore(),
                eviction_policy=SizeBasedEviction(max_size=5),
                context_chain=ContextChainFactory(),
            )

    def test_runtime_accepts_surface_backend_with_noop_eviction(self) -> None:
        from hecate.runtime.context_policy import ContextChainFactory

        PregelRuntime(
            self._graph([{"type": "compression", "params": {"backend": "surface_replacement"}}]),
            object(),
            InMemoryCheckpointStore(),
            context_chain=ContextChainFactory(),
        )

    def test_checkpoint_metadata_stamps_last_compaction(self) -> None:
        from hecate.runtime.context_policy import ContextChainFactory

        factory = ContextChainFactory()
        runtime = PregelRuntime(
            self._graph(),
            object(),
            InMemoryCheckpointStore(),
            context_chain=factory,
        )
        assert runtime._compaction_checkpoint_metadata(uuid.uuid4()) == {}
        sid = uuid.uuid4()
        factory._note_compaction(str(sid), "c-42")
        assert runtime._compaction_checkpoint_metadata(sid) == {"last_compaction_id": "c-42"}
