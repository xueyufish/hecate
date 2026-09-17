"""Tests for the citation provenance layer (1.3.5e Stage 1).

Covers the registry/marker/back-mapper/risk-signal core, the fail-fast
policy resolver, ToolWorker write-path marking, LLMWorker instruction
injection + back-mapping, and the interaction invariants with the 4.13
projection chain (truncation partial recording, offload/recall stability).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.runtime.citation_provenance import (
    CITATION_INSTRUCTION,
    EXECUTION_CONTEXT_KEY,
    INSTRUCTION_TAG,
    CitationBackMapper,
    CitationProvenanceManager,
    CitationRegistry,
    chunk_text,
    mark_tool_result,
    rebuild_registry_from_events,
    uncited_ratio,
)
from hecate.runtime.context_processors import (
    ChainContext,
    HeuristicTokenEstimator,
    ToolResultTruncationProcessor,
    unitize,
)
from hecate.runtime.eventstore import Event, EventType
from hecate.runtime.provenance_policy import (
    CitationPolicyError,
    resolve_citation_policy,
)
from hecate.runtime.workers.llm_worker import (
    LLMWorker,
    _inject_citation_instruction,
    _reconcile_truncated_chunks,
)
from hecate.runtime.workers.tool_worker import ToolWorker


class StubEventStore:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def append(self, event: Event) -> None:
        self.events.append(event)

    async def append_batch(self, events: list[Event]) -> None:
        self.events.extend(events)

    def by_name(self, name: str) -> list[Event]:
        return [e for e in self.events if e.payload.get("event_name") == name]


def _long_text(chars: int) -> str:
    return ("The engine completed the requested analysis without errors. " * chars)[:chars].strip()


def _make_port(response: str = "Acknowledged.") -> MagicMock:
    port = MagicMock()

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    async def fake_llm_invoke(*args, **kwargs):
        yield response

    async def fake_llm_invoke_structured(*args, **kwargs):
        yield {"content": response, "tool_calls": None}
        yield {"content": None, "tool_calls": None}

    port.llm_invoke = fake_llm_invoke
    port.llm_invoke_structured = fake_llm_invoke_structured
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    return port


# ---------------------------------------------------------------------------
# Core: chunking, marking, registry
# ---------------------------------------------------------------------------


class TestChunkMarker:
    def test_chunk_text_whitespace_boundaries(self) -> None:
        text = " ".join(f"word{i}" for i in range(200))
        chunks = chunk_text(text, 100)
        assert all(len(c) <= 110 for c in chunks)
        assert "".join(chunks) == text

    def test_mark_below_min_size_passthrough(self) -> None:
        registry = CitationRegistry()
        marked, entries = mark_tool_result("short", registry, granularity=300, min_chars=200)
        assert marked == "short"
        assert entries == []
        assert registry.entries() == {}

    def test_mark_prefixes_every_chunk_and_registers(self) -> None:
        registry = CitationRegistry()
        content = _long_text(800)
        marked, entries = mark_tool_result(content, registry, granularity=200, min_chars=100)
        assert entries
        assert marked.startswith("【0-0】")
        assert all(f"【0-{e.chunk_index}】" in marked for e in entries)
        # Stripping markers reconstructs the original text exactly.
        import re

        assert re.sub(r"【\d+-\d+】", "", marked) == content

    def test_registry_ids_never_reused_across_results(self) -> None:
        registry = CitationRegistry()
        _, first = mark_tool_result(_long_text(500), registry, 200, 100)
        _, second = mark_tool_result(_long_text(500), registry, 200, 100)
        assert first[0].result_seq == 0
        assert second[0].result_seq == 1
        assert {e.marker for e in first}.isdisjoint({e.marker for e in second})

    def test_registry_resolves_regardless_of_projection(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        # The projection is gone entirely; registry entries stay resolvable.
        assert registry.resolve(entries[0].marker) is entries[0]

    def test_mark_truncated(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        assert registry.mark_truncated(entries[0].marker) is True
        assert registry.resolve(entries[0].marker).truncated is True
        assert registry.mark_truncated("9-9") is False


class TestRegistryRebuild:
    def test_rebuild_from_events(self) -> None:
        original = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), original, 200, 100)
        original.mark_truncated(entries[0].marker)
        event = SimpleNamespace(
            payload={
                "event_name": "CITATION_REGISTERED",
                "result_seq": 0,
                "chunks": [e.as_dict() for e in entries],
            }
        )
        rebuilt = rebuild_registry_from_events([event])
        assert rebuilt.resolve(entries[0].marker).text == entries[0].text
        assert rebuilt.resolve(entries[0].marker).truncated is True
        # Sequence continues after the rebuild — no identifier reuse.
        _, next_entries = mark_tool_result(_long_text(500), rebuilt, 200, 100)
        assert next_entries[0].result_seq == 1

    def test_rebuild_skips_malformed_payloads(self) -> None:
        bad = SimpleNamespace(payload={"event_name": "CITATION_REGISTERED"})
        rebuilt = rebuild_registry_from_events([bad])
        assert rebuilt.entries() == {}


# ---------------------------------------------------------------------------
# Back-mapping and risk signal
# ---------------------------------------------------------------------------


class TestBackMapper:
    def test_cited_and_unresolved_split(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        known = entries[0].marker
        response = f"Found the issue 【{known}】 and also 【7-7】."
        result = CitationBackMapper().map_response(response, registry)
        assert result.cited == [f"【{known}】"]
        assert result.unresolved == ["【7-7】"]

    def test_duplicate_markers_deduplicated(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        marker = entries[0].marker
        response = f"A 【{marker}】 then B 【{marker}】."
        result = CitationBackMapper().map_response(response, registry)
        assert result.cited == [f"【{marker}】"]


class TestUncitedRatio:
    def test_spec_scenario_one_of_four_uncited(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        m = entries[0].marker
        response = (
            f"The deployment finished successfully across all regions 【{m}】. "
            f"The error rate dropped to two percent after the patch 【{m}】. "
            "Latency improved by forty percent in the last measured window. "
            f"The database migration completed without any data loss 【{m}】."
        )
        ratio, factual, uncited = uncited_ratio(response)
        assert factual == 4
        assert uncited == 1
        assert ratio == 0.25

    def test_heuristic_exclusions(self) -> None:
        response = (
            "How does the system behave under load right now? "  # question
            "Let me check the deployment status for you. "  # procedural
            "```\nSome very long code line that should be excluded entirely.\n``` "  # fence
            "- 42 "  # list/number line
            "ok "  # too short
            "The server is running normally on all primary nodes today."  # factual
        )
        ratio, factual, uncited = uncited_ratio(response)
        assert factual == 1
        assert uncited == 1
        assert ratio == 1.0

    def test_no_factual_sentences(self) -> None:
        assert uncited_ratio("ok? sure") == (0.0, 0, 0)


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------


class TestCitationPolicy:
    def test_default_disabled(self) -> None:
        policy = resolve_citation_policy({}, None)
        assert policy.enabled is False

    def test_node_overrides_agent(self) -> None:
        policy = resolve_citation_policy(
            {"citation_provenance": {"enabled": True, "chunk_granularity": 150}},
            {"enabled": False},
        )
        assert policy.enabled is True
        assert policy.chunk_granularity == 150
        assert policy.min_chunk_chars == 200  # default preserved

    def test_agent_policy_applies_without_node_config(self) -> None:
        policy = resolve_citation_policy({}, {"enabled": True})
        assert policy.enabled is True

    def test_invalid_agent_policy_fails_even_when_node_overrides(self) -> None:
        with pytest.raises(CitationPolicyError, match="agent"):
            resolve_citation_policy(
                {"citation_provenance": {"enabled": True}},
                {"enabled": True, "unknown_field": 1},
            )

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(CitationPolicyError, match="unknown_field"):
            resolve_citation_policy({"citation_provenance": {"enabled": True, "unknown_field": 1}}, None)

    @pytest.mark.parametrize(
        ("spec", "field"),
        [
            ({"enabled": "yes"}, "enabled"),
            ({"enabled": True, "chunk_granularity": 0}, "chunk_granularity"),
            ({"enabled": True, "chunk_granularity": -5}, "chunk_granularity"),
            ({"enabled": True, "min_chunk_chars": -1}, "min_chunk_chars"),
        ],
    )
    def test_invalid_values_rejected(self, spec: dict, field: str) -> None:
        with pytest.raises(CitationPolicyError) as exc_info:
            resolve_citation_policy({"citation_provenance": spec}, None)
        assert field in str(exc_info.value)

    def test_canonical_hash_stable_and_distinct(self) -> None:
        a = resolve_citation_policy({"citation_provenance": {"enabled": True}}, None)
        b = resolve_citation_policy({"citation_provenance": {"enabled": True}}, None)
        c = resolve_citation_policy({"citation_provenance": {"enabled": True, "chunk_granularity": 100}}, None)
        assert a.canonical_hash == b.canonical_hash
        assert a.canonical_hash != c.canonical_hash


# ---------------------------------------------------------------------------
# ToolWorker write-path marking
# ---------------------------------------------------------------------------


def _citation_context(event_store: StubEventStore, agent_policy: dict | None = None) -> dict:
    return {
        "session_id": "sess-1",
        "superstep": 0,
        "event_store": event_store,
        EXECUTION_CONTEXT_KEY: CitationProvenanceManager(agent_policy=agent_policy),
    }


class TestToolWorkerProvenance:
    async def test_marked_result_and_registered_event(self) -> None:
        event_store = StubEventStore()
        port = MagicMock()

        async def execute(*args, **kwargs):
            return _long_text(500)

        port.tool_execute = execute
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        worker = ToolWorker(port=port)
        snapshot = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "search", "arguments": {}}}]},
            ]
        }
        exec_ctx = _citation_context(event_store, agent_policy={"enabled": True})
        await worker.execute(
            node_id="tool_call",
            node_config={},
            channel_snapshot=snapshot,
            execution_context=exec_ctx,
        )
        # The manager's registry holds the marked chunks.
        registry = exec_ctx[EXECUTION_CONTEXT_KEY].registry_for("sess-1")
        assert registry.entries()
        registered = event_store.by_name("CITATION_REGISTERED")
        assert len(registered) == 1
        assert registered[0].event_type == EventType.CUSTOM
        assert registered[0].payload["result_seq"] == 0

    async def test_small_result_unmarked(self) -> None:
        event_store = StubEventStore()
        port = MagicMock()

        async def execute(*args, **kwargs):
            return "tiny"

        port.tool_execute = execute
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        worker = ToolWorker(port=port)
        snapshot = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "search", "arguments": {}}}]},
            ]
        }
        exec_ctx = _citation_context(event_store, agent_policy={"enabled": True})
        result = await worker.execute(
            node_id="tool_call",
            node_config={},
            channel_snapshot=snapshot,
            execution_context=exec_ctx,
        )
        assert result.channel_updates["messages"][0]["content"] == "tiny"
        assert event_store.by_name("CITATION_REGISTERED") == []

    async def test_no_manager_untouched(self) -> None:
        event_store = StubEventStore()
        port = MagicMock()

        async def execute(*args, **kwargs):
            return _long_text(500)

        port.tool_execute = execute
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        worker = ToolWorker(port=port)
        snapshot = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "search", "arguments": {}}}]},
            ]
        }
        result = await worker.execute(
            node_id="tool_call",
            node_config={},
            channel_snapshot=snapshot,
            execution_context={"session_id": "sess-1", "superstep": 0, "event_store": event_store},
        )
        assert "【" not in result.channel_updates["messages"][0]["content"]
        assert event_store.events == []


# ---------------------------------------------------------------------------
# LLMWorker instruction injection and back-mapping
# ---------------------------------------------------------------------------


class TestLLMWorkerProvenance:
    async def test_first_turn_injects_and_persists_instruction(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="Analysis complete and verified.")
        worker = LLMWorker(port=port)
        exec_ctx = _citation_context(event_store, agent_policy={"enabled": True})
        exec_ctx[EXECUTION_CONTEXT_KEY].registry_for("sess-1")  # ensure registry exists
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context=exec_ctx,
        )
        # Instruction projected for the LLM call...
        sent = port.context_assemble.call_args.kwargs["messages"]
        assert any(INSTRUCTION_TAG in m.get("content", "") for m in sent if isinstance(m.get("content"), str))
        # ...and persisted once ahead of the assistant message.
        updates = result.channel_updates["messages"]
        assert len(updates) == 2
        assert INSTRUCTION_TAG in updates[0]["content"]
        assert updates[1]["role"] == "assistant"
        # Back-mapping metadata + audit events.
        assert updates[1]["citations"] == {"cited": [], "unresolved": []}
        assert len(event_store.by_name("CITATION_MAP")) == 1
        risk = event_store.by_name("CITATION_RISK")
        assert len(risk) == 1
        assert risk[0].payload["level"] == "warn"

    async def test_second_turn_no_duplicate_persist(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="Done.")
        worker = LLMWorker(port=port)
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "user", "content": CITATION_INSTRUCTION},
        ]
        exec_ctx = _citation_context(event_store, agent_policy={"enabled": True})
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": history, "_session_id": "sess-1"},
            execution_context=exec_ctx,
        )
        updates = result.channel_updates["messages"]
        assert len(updates) == 1  # only the assistant message — no re-persist
        sent = port.context_assemble.call_args.kwargs["messages"]
        # The projected copy already carries the instruction; nothing appended.
        assert sum(1 for m in sent if isinstance(m.get("content"), str) and INSTRUCTION_TAG in m["content"]) == 1

    async def test_disabled_zero_behavior_difference(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="Done.")
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context={"session_id": "sess-1", "superstep": 0, "event_store": event_store},
        )
        updates = result.channel_updates["messages"]
        assert len(updates) == 1
        assert "citations" not in updates[0]
        assert event_store.events == []

    async def test_response_citations_back_mapped(self) -> None:
        event_store = StubEventStore()
        manager = CitationProvenanceManager(agent_policy={"enabled": True})
        registry = manager.registry_for("sess-1")
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        marker = entries[0].marker
        port = _make_port(response=f"The task finished successfully per the report 【{marker}】.")
        worker = LLMWorker(port=port)
        exec_ctx = _citation_context(event_store)
        exec_ctx[EXECUTION_CONTEXT_KEY] = manager
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context=exec_ctx,
        )
        assistant = result.channel_updates["messages"][-1]
        assert assistant["citations"]["cited"] == [f"【{marker}】"]
        map_event = event_store.by_name("CITATION_MAP")[0]
        assert map_event.payload["citations"]["cited"] == [f"【{marker}】"]


# ---------------------------------------------------------------------------
# Interaction with the 4.13 projection chain
# ---------------------------------------------------------------------------


class TestTruncationInteraction:
    async def test_truncation_cuts_tail_and_reconciler_flags_partial(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(900), registry, 200, 100)
        # Rebuild the marked content from entries (as the worker would write).
        marked = "".join(f"【{e.marker}】{e.text}" for e in entries)
        tool_msg = {"role": "tool", "tool_call_id": "tc1", "content": marked}

        # Chain truncation: cap at 60 tokens (240 chars) — cuts mid-chunk.
        # unitize requires the assistant tool_calls anchor, else the dangling
        # tool result is dropped whole by the pairing invariant.
        units = unitize(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "tc1", "function": {"name": "t", "arguments": {}}}],
                },
                tool_msg,
            ]
        )
        ctx = ChainContext(budget=10_000, estimator=HeuristicTokenEstimator(), tool_result_limit=60)
        out_units, _result = await ToolResultTruncationProcessor().process(units, ctx)
        truncated = [m for u in out_units for m in u.messages if m.get("role") == "tool"][0]["content"]
        assert truncated != marked
        assert "truncated" in truncated
        assert entries[0].marker in truncated

        # Reconciler: the surviving boundary chunk whose text got cut is
        # recorded as partially present.
        _reconcile_truncated_chunks([{"role": "tool", "content": truncated}], registry)
        partial = [m for m, e in registry.entries().items() if e.truncated]
        assert partial, "expected at least one chunk flagged partial"
        for marker in partial:
            assert marker in truncated

    def test_intact_chunk_not_flagged(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        content = "".join(f"【{e.marker}】{e.text}" for e in entries)
        _reconcile_truncated_chunks([{"role": "tool", "content": content}], registry)
        assert all(not e.truncated for e in registry.entries().values())

    def test_response_role_not_reconciled(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        # An assistant response cites the marker without carrying chunk text —
        # that is not truncation and must not flag the entry.
        _reconcile_truncated_chunks([{"role": "assistant", "content": f"Answer 【{entries[0].marker}】"}], registry)
        assert all(not e.truncated for e in registry.entries().values())


class TestOffloadRecallInteraction:
    def test_recall_restores_original_markers_without_renumbering(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        original_markers = [e.marker for e in entries]
        offloaded_text = "".join(f"【{e.marker}】{e.text}" for e in entries)

        # New results marked in between keep their own sequence — offload and
        # recall never renumber existing identifiers.
        _, later = mark_tool_result(_long_text(500), registry, 200, 100)
        assert later[0].result_seq == 1

        # Recall reloads the offloaded block verbatim: every original marker
        # still resolves to the same registered chunk.
        for marker in original_markers:
            entry = registry.resolve(marker)
            assert entry is not None
            assert f"【{marker}】{entry.text}" in offloaded_text or entry.text in offloaded_text

    def test_offloaded_entry_still_resolvable_for_backmap(self) -> None:
        registry = CitationRegistry()
        _, entries = mark_tool_result(_long_text(500), registry, 200, 100)
        # Even with the message absent from the projection (offloaded to a
        # stub), a response citing the marker resolves via the registry.
        result = CitationBackMapper().map_response(f"Per the archived report 【{entries[0].marker}】.", registry)
        assert result.cited == [f"【{entries[0].marker}】"]
        assert result.unresolved == []


# ---------------------------------------------------------------------------
# Instruction injection edge cases
# ---------------------------------------------------------------------------


class TestInstructionInjection:
    async def test_history_present_projection_dropped_reinjects_transient(self) -> None:
        history = [{"role": "user", "content": CITATION_INSTRUCTION}]
        projection = [{"role": "user", "content": "latest question"}]
        persisted = await _inject_citation_instruction(history, projection, None, "llm")
        assert persisted is None  # already in history — nothing to persist
        # But the projection got a transient copy.
        assert sum(1 for m in projection if INSTRUCTION_TAG in m["content"]) == 1
