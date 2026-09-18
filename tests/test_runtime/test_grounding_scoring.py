"""Tests for the grounding scoring layer (1.3.5e Stage 2).

Covers the policy resolver (fail-fast, precedence, canonical hash), the
scorer seam (LLM judge batching/degradation, HTTP NLI mapping including the
binary shape, escalation), evidence acquisition (registry lookup, fallback
retrieval), aggregation + the would_block shadow disposition, the LLM
worker's LLM_RESPONSE chain wiring with behavior equivalence against the
legacy hook path, and the observation-only contract.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.runtime.citation_provenance import CitationProvenanceManager, mark_tool_result
from hecate.runtime.eventstore import Event
from hecate.runtime.grounding_policy import (
    GroundingPolicyError,
    resolve_grounding_policy,
)
from hecate.runtime.grounding_scoring import (
    EVENT_NAME_SCORE,
    ClaimEvidencePair,
    EscalatingScorer,
    GroundingScorer,
    HttpNliScorer,
    LlmJudgeScorer,
    ScoringResult,
    acquire_fallback_evidence,
    aggregate_results,
    build_claim_pairs,
    build_scorer,
    build_scoring_payload,
    score_response,
    score_triggered,
)
from hecate.runtime.guardrail import GuardrailAction, GuardrailResult
from hecate.runtime.middleware_factory import build_llm_chain
from hecate.runtime.workers.llm_worker import LLMWorker

JUDGE_PROMPT_TAG = "fact-checking judge"

_JUDGE_JSON = (
    '[{"index": 1, "verdict": "supported", "confidence": 0.9},'
    ' {"index": 2, "verdict": "contradicted", "confidence": 0.8}]'
)


class StubEventStore:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def append(self, event: Event) -> None:
        self.events.append(event)

    async def append_batch(self, events: list[Event]) -> None:
        self.events.extend(events)

    def by_name(self, name: str) -> list[Event]:
        return [e for e in self.events if e.payload.get("event_name") == name]


def _judge_reply(payload: str):
    async def fake_llm_invoke(messages, config):
        content = messages[-1]["content"] if messages else ""
        if JUDGE_PROMPT_TAG in content:
            yield payload
        else:
            yield "main response"

    return fake_llm_invoke


def _make_port(response: str = "Acknowledged.", judge_payload: str | None = None) -> MagicMock:
    port = MagicMock()

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    async def fake_llm_invoke(messages, config):
        content = messages[-1]["content"] if messages else ""
        if judge_payload is not None and JUDGE_PROMPT_TAG in content:
            yield judge_payload
        else:
            yield response

    port.llm_invoke = fake_llm_invoke
    port.llm_invoke_structured = None
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    port.knowledge_query = AsyncMock(return_value=[])
    return port


def _long_text(chars: int) -> str:
    return ("Hecate is a multi-tenant agent platform built with FastAPI and SQLAlchemy. " * 20)[:chars]


def _pairs(n: int, claim: str = "claim", evidence: str = "evidence") -> list[ClaimEvidencePair]:
    return [ClaimEvidencePair(claim=f"{claim} {i}", evidences=[evidence]) for i in range(n)]


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------


class TestGroundingPolicy:
    def test_default_disabled(self) -> None:
        policy = resolve_grounding_policy({}, None)
        assert policy.enabled is False
        assert policy.backend == "llm_judge"
        assert policy.trigger == "on_uncited"

    def test_node_config_enables(self) -> None:
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True}})
        assert policy.enabled is True
        assert policy.canonical_hash

    def test_agent_precedes_when_node_absent(self) -> None:
        policy = resolve_grounding_policy({}, {"enabled": True, "trigger": "always"})
        assert policy.enabled is True
        assert policy.trigger == "always"

    def test_node_overrides_agent(self) -> None:
        policy = resolve_grounding_policy(
            {"grounding_scoring": {"enabled": True, "trigger": "always"}},
            {"enabled": True, "trigger": "sample", "sample_rate": 0.5},
        )
        assert policy.trigger == "always"
        assert policy.sample_rate == 1.0

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(GroundingPolicyError, match="unknown field"):
            resolve_grounding_policy({"grounding_scoring": {"enabled": True, "wat": 1}})

    def test_nested_unknown_field_rejected(self) -> None:
        with pytest.raises(GroundingPolicyError, match="fallback"):
            resolve_grounding_policy({"grounding_scoring": {"enabled": True, "fallback": {"nope": 1}}})

    def test_http_nli_requires_endpoint(self) -> None:
        with pytest.raises(GroundingPolicyError, match="nli_endpoint"):
            resolve_grounding_policy({"grounding_scoring": {"enabled": True, "backend": "http_nli"}})

    def test_invalid_sample_rate_rejected(self) -> None:
        with pytest.raises(GroundingPolicyError, match="sample_rate"):
            resolve_grounding_policy({"grounding_scoring": {"enabled": True, "trigger": "sample", "sample_rate": 5}})

    def test_invalid_agent_surfaced_despite_node(self) -> None:
        with pytest.raises(GroundingPolicyError, match="agent"):
            resolve_grounding_policy(
                {"grounding_scoring": {"enabled": True}},
                {"enabled": True, "nope": 1},
            )

    def test_canonical_hash_stable(self) -> None:
        spec = {"grounding_scoring": {"enabled": True, "fallback": {"enabled": True, "kb_ids": ["a"]}}}
        assert resolve_grounding_policy(spec).canonical_hash == resolve_grounding_policy(spec).canonical_hash
        other = resolve_grounding_policy({"grounding_scoring": {"enabled": True}})
        assert other.canonical_hash != resolve_grounding_policy(spec).canonical_hash


# ---------------------------------------------------------------------------
# Scorer seam
# ---------------------------------------------------------------------------


class TestScorerSeam:
    def test_abstract_not_instantiable(self) -> None:
        with pytest.raises(TypeError):
            GroundingScorer()  # type: ignore[abstract]

    async def test_judge_batch_three_way(self) -> None:
        scorer = LlmJudgeScorer(port=MagicMock(llm_invoke=_judge_reply(_JUDGE_JSON)), model="m")
        results = await scorer.score(_pairs(2))
        assert [r.verdict for r in results] == ["supported", "contradicted"]
        assert results[0].confidence == 0.9

    async def test_judge_red_line_low_confidence_stays_supported(self) -> None:
        payload = '[{"index": 1, "verdict": "supported", "confidence": 0.1}]'
        scorer = LlmJudgeScorer(port=MagicMock(llm_invoke=_judge_reply(payload)), model="m")
        results = await scorer.score(_pairs(1))
        assert results[0].verdict == "supported"
        assert results[0].confidence == 0.1

    async def test_judge_fenced_json_parsed(self) -> None:
        payload = "```json\n" + _JUDGE_JSON + "\n```"
        scorer = LlmJudgeScorer(port=MagicMock(llm_invoke=_judge_reply(payload)), model="m")
        results = await scorer.score(_pairs(2))
        assert [r.verdict for r in results] == ["supported", "contradicted"]

    async def test_judge_retries_then_degrades(self) -> None:
        calls = 0

        async def always_junk(messages, config):
            nonlocal calls
            calls += 1
            yield "not json at all"

        scorer = LlmJudgeScorer(port=MagicMock(llm_invoke=always_junk), model="m")
        results = await scorer.score(_pairs(2))
        assert calls == 2  # one retry
        assert all(r.degraded for r in results)

    async def test_judge_missing_index_degrades_single_pair(self) -> None:
        payload = '[{"index": 2, "verdict": "supported", "confidence": 0.9}]'
        scorer = LlmJudgeScorer(port=MagicMock(llm_invoke=_judge_reply(payload)), model="m")
        results = await scorer.score(_pairs(2))
        assert results[0].degraded is True
        assert results[1].degraded is False


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    next_payload: object = None
    fail = False
    last_request: dict | None = None

    def __init__(self, timeout=None) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        type(self).last_request = {"url": url, "json": json}
        if type(self).fail:
            raise RuntimeError("endpoint down")
        return _FakeResponse(type(self).next_payload)


@pytest.fixture()
def fake_http_client(monkeypatch):
    import hecate.runtime.grounding_scoring as gs

    _FakeAsyncClient.next_payload = None
    _FakeAsyncClient.fail = False
    _FakeAsyncClient.last_request = None
    monkeypatch.setattr(gs.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


class TestHttpNliScorer:
    async def test_three_way_mapping(self, fake_http_client) -> None:
        fake_http_client.next_payload = {
            "results": [
                {"verdict": "supported", "confidence": 0.95},
                {"verdict": "contradicted", "confidence": 0.7},
            ]
        }
        scorer = HttpNliScorer("http://nli.internal/score")
        results = await scorer.score(_pairs(2))
        assert [r.verdict for r in results] == ["supported", "contradicted"]

    async def test_binary_shape_never_contradicts(self, fake_http_client) -> None:
        fake_http_client.next_payload = {"results": [{"support": True}, {"support": False}]}
        scorer = HttpNliScorer("http://nli.internal/score")
        results = await scorer.score(_pairs(2))
        assert results[0].verdict == "supported"
        assert results[0].confidence == 0.8
        assert results[1].verdict == "unverifiable"

    async def test_endpoint_failure_degrades_all(self, fake_http_client) -> None:
        fake_http_client.fail = True
        scorer = HttpNliScorer("http://nli.internal/score")
        results = await scorer.score(_pairs(2))
        assert all(r.degraded for r in results)

    async def test_missing_result_item_degrades(self, fake_http_client) -> None:
        fake_http_client.next_payload = {"results": [{"verdict": "supported", "confidence": 0.9}]}
        scorer = HttpNliScorer("http://nli.internal/score")
        results = await scorer.score(_pairs(2))
        assert results[0].verdict == "supported"
        assert results[1].degraded is True

    async def test_request_body_shape(self, fake_http_client) -> None:
        fake_http_client.next_payload = {"results": [{"support": False}]}
        scorer = HttpNliScorer("http://nli.internal/score")
        await scorer.score(_pairs(1, evidence="chunk text"))
        body = fake_http_client.last_request["json"]
        assert body["pairs"][0]["claim"].startswith("claim")
        assert body["pairs"][0]["evidence"] == "chunk text"


class _PrimaryScorer(GroundingScorer):
    def __init__(self, confidences: list[float]) -> None:
        self._confidences = confidences

    async def score(self, pairs):
        return [
            ScoringResult(
                claim=pair.claim,
                verdict="supported",
                confidence=self._confidences[i],
                evidence_kind="cited",
            )
            for i, pair in enumerate(pairs)
        ]


class TestEscalatingScorer:
    async def test_low_confidence_escalated(self) -> None:
        primary = _PrimaryScorer([0.2, 0.9])
        judge = LlmJudgeScorer(port=MagicMock(llm_invoke=_judge_reply(_JUDGE_JSON)), model="m")
        from hecate.runtime.grounding_policy import EscalationConfig

        scorer = EscalatingScorer(primary, judge, EscalationConfig(enabled=True, confidence=0.5, max_pairs=8))
        results = await scorer.score(_pairs(2))
        assert results[0].confidence == 0.9  # replaced by judge
        assert results[1].confidence == 0.9  # untouched

    async def test_max_pairs_cap(self) -> None:
        primary = _PrimaryScorer([0.1, 0.1, 0.1])
        seen: list[ClaimEvidencePair] = []

        class CountingJudge(GroundingScorer):
            async def score(self, pairs):
                seen.extend(pairs)
                return [
                    ScoringResult(claim=p.claim, verdict="unverifiable", confidence=0.0, evidence_kind="cited")
                    for p in pairs
                ]

        from hecate.runtime.grounding_policy import EscalationConfig

        scorer = EscalatingScorer(primary, CountingJudge(), EscalationConfig(enabled=True, confidence=0.5, max_pairs=1))
        results = await scorer.score(_pairs(3))
        assert len(seen) == 1
        assert sum(1 for r in results if r.verdict == "unverifiable") >= 1

    async def test_disabled_no_escalation(self) -> None:
        primary = _PrimaryScorer([0.1])
        called = 0

        class NoJudge(GroundingScorer):
            async def score(self, pairs):
                nonlocal called
                called += 1
                return []

        from hecate.runtime.grounding_policy import EscalationConfig

        scorer = EscalatingScorer(primary, NoJudge(), EscalationConfig(enabled=False))
        results = await scorer.score(_pairs(1))
        assert called == 0
        assert results[0].confidence == 0.1

    def test_build_scorer_selects_backend(self) -> None:
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True}})
        assert isinstance(build_scorer(policy, port=MagicMock(), default_model="m"), LlmJudgeScorer)
        nli_policy = resolve_grounding_policy(
            {"grounding_scoring": {"enabled": True, "backend": "http_nli", "nli_endpoint": "http://x"}}
        )
        assert isinstance(build_scorer(nli_policy, port=MagicMock(), default_model="m"), HttpNliScorer)


# ---------------------------------------------------------------------------
# Evidence acquisition and aggregation
# ---------------------------------------------------------------------------


class TestEvidenceAcquisition:
    def test_cited_evidence_resolved(self) -> None:
        manager = CitationProvenanceManager()
        registry = manager.registry_for("s")
        _, entries = mark_tool_result(_long_text(600), registry, 200, 100)
        text = f"The task finished successfully per the report 【{entries[0].marker}】."
        pairs, uncited = build_claim_pairs(text, registry)
        assert len(pairs) == 1
        assert pairs[0].evidences == [entries[0].text]
        assert pairs[0].refs[0].ref == entries[0].marker
        assert uncited == []

    def test_invented_marker_goes_uncited(self) -> None:
        text = "The task finished successfully per the report 【9-9】."
        pairs, uncited = build_claim_pairs(text, CitationProvenanceManager().registry_for("s"))
        assert pairs == []
        assert len(uncited) == 1

    def test_none_registry_all_uncited(self) -> None:
        text = "The task finished successfully per the report 【0-0】."
        pairs, uncited = build_claim_pairs(text, None)
        assert pairs == []
        assert len(uncited) == 1

    async def test_fallback_retrieval(self) -> None:
        port = MagicMock()
        port.knowledge_query = AsyncMock(return_value=[{"content": "retrieved chunk"}])
        kb = str(uuid.uuid4())
        result = await acquire_fallback_evidence(port, ["uncited claim here"], [kb], top_k=3)
        assert result == {"uncited claim here": ["retrieved chunk"]}
        called_kb = port.knowledge_query.call_args.kwargs["kb_ids"]
        assert len(called_kb) == 1

    async def test_fallback_invalid_kb_skipped(self) -> None:
        port = MagicMock()
        port.knowledge_query = AsyncMock(return_value=[])
        result = await acquire_fallback_evidence(port, ["claim"], ["not-a-uuid"], top_k=3)
        assert result == {}
        port.knowledge_query.assert_not_called()

    async def test_fallback_retrieval_failure_degrades(self) -> None:
        port = MagicMock()
        port.knowledge_query = AsyncMock(side_effect=RuntimeError("kb down"))
        result = await acquire_fallback_evidence(port, ["claim"], [str(uuid.uuid4())], top_k=3)
        assert result == {}

    async def test_fallback_top_k_applied(self) -> None:
        port = MagicMock()
        port.knowledge_query = AsyncMock(return_value=[{"content": f"c{i}"} for i in range(10)])
        result = await acquire_fallback_evidence(port, ["claim"], [str(uuid.uuid4())], top_k=2)
        assert len(result["claim"]) == 2


class TestAggregationAndShadow:
    def _results(self):
        from hecate.runtime.grounding_scoring import ScoringResult

        return [
            ScoringResult(claim="a", verdict="supported", confidence=0.9, evidence_kind="cited"),
            ScoringResult(claim="b", verdict="contradicted", confidence=0.8, evidence_kind="cited"),
            ScoringResult(claim="c", verdict="unverifiable", confidence=0.0, evidence_kind="none"),
        ]

    def test_aggregates(self) -> None:
        agg = aggregate_results(self._results())
        assert agg["claims_total"] == 3
        assert agg["contradicted"] == 1
        assert agg["contradicted_ratio"] == round(1 / 3, 4)
        assert agg["unverifiable_ratio"] == round(1 / 3, 4)

    def test_would_block_any_contradicted(self) -> None:
        from hecate.runtime.grounding_policy import ShadowThresholds

        payload = build_scoring_payload(self._results(), ShadowThresholds())
        assert payload["would_block"]["triggered"] is True
        assert "any_claim_contradicted" in payload["would_block"]["reason"]

    def test_would_block_clean_response(self) -> None:
        from hecate.runtime.grounding_policy import ShadowThresholds
        from hecate.runtime.grounding_scoring import ScoringResult

        results = [ScoringResult(claim="a", verdict="supported", confidence=0.95, evidence_kind="cited")]
        payload = build_scoring_payload(results, ShadowThresholds())
        assert payload["would_block"]["triggered"] is False


class TestScoreResponse:
    async def test_no_factual_sentences_returns_none(self) -> None:
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True}})
        assert await score_response(policy, None, MagicMock(), "How?\n\n```code```", "m") is None

    async def test_fallback_disabled_uncited_unverifiable_without_calls(self) -> None:
        port = MagicMock()
        port.llm_invoke = MagicMock(side_effect=AssertionError("no backend calls expected"))
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True}})
        payload = await score_response(policy, None, port, "The platform was founded in 1998 by penguins.", "m")
        assert payload["per_claim"][0]["verdict"] == "unverifiable"
        assert payload["per_claim"][0]["evidence"]["kind"] == "none"

    async def test_cited_claim_scored_via_judge(self) -> None:
        manager = CitationProvenanceManager()
        registry = manager.registry_for("s")
        _, entries = mark_tool_result(_long_text(600), registry, 200, 100)
        port = _make_port(response="x", judge_payload=_JUDGE_JSON)
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True, "trigger": "always"}})
        text = f"The platform is built with FastAPI and SQLAlchemy 【{entries[0].marker}】."
        payload = await score_response(policy, registry, port, text, "gpt-4o")
        assert payload["per_claim"][0]["verdict"] == "supported"

    async def test_fallback_enabled_routes_uncited_to_retrieval(self) -> None:
        async def fake_llm_invoke(messages, config):
            if JUDGE_PROMPT_TAG in (messages[-1]["content"] if messages else ""):
                yield '[{"index": 1, "verdict": "supported", "confidence": 0.85}]'
            else:
                yield "x"

        port = MagicMock()
        port.llm_invoke = fake_llm_invoke
        port.knowledge_query = AsyncMock(return_value=[{"content": "the platform was founded in 2023 by researchers"}])
        policy = resolve_grounding_policy(
            {
                "grounding_scoring": {
                    "enabled": True,
                    "trigger": "always",
                    "fallback": {"enabled": True, "kb_ids": [str(uuid.uuid4())]},
                }
            }
        )
        payload = await score_response(policy, None, port, "The platform was founded in 2023 by researchers.", "m")
        assert payload["per_claim"][0]["evidence"]["kind"] == "fallback"
        assert payload["per_claim"][0]["verdict"] == "supported"


class TestScoringTrigger:
    def test_on_uncited(self) -> None:
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True}})
        assert score_triggered(policy, "The platform was founded in 1998 by penguins.") is True
        manager = CitationProvenanceManager()
        registry = manager.registry_for("s")
        _, entries = mark_tool_result(_long_text(600), registry, 200, 100)
        assert score_triggered(policy, f"Everything is grounded 【{entries[0].marker}】.") is False

    def test_always(self) -> None:
        policy = resolve_grounding_policy({"grounding_scoring": {"enabled": True, "trigger": "always"}})
        manager = CitationProvenanceManager()
        registry = manager.registry_for("s")
        _, entries = mark_tool_result(_long_text(600), registry, 200, 100)
        assert score_triggered(policy, f"Everything is grounded 【{entries[0].marker}】.") is True

    def test_sample_bounds(self) -> None:
        always_fire = resolve_grounding_policy(
            {"grounding_scoring": {"enabled": True, "trigger": "sample", "sample_rate": 1.0}}
        )
        never_fire = resolve_grounding_policy(
            {"grounding_scoring": {"enabled": True, "trigger": "sample", "sample_rate": 0.0}}
        )
        text = "The platform was founded in 1998 by penguins."
        assert score_triggered(always_fire, text) is True
        assert score_triggered(never_fire, text) is False


# ---------------------------------------------------------------------------
# LLM worker chain wiring + observation contract
# ---------------------------------------------------------------------------


class _BlockingPostHook:
    async def on_post_llm_call(self, response, messages):
        return GuardrailResult(action=GuardrailAction.BLOCK, reason="policy")


class _SanitizingPostHook:
    async def on_post_llm_call(self, response, messages):
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            modified_data={"response": {**response, "content": "sanitized content"}},
        )


def _scoring_ctx(event_store: StubEventStore, kb_stub: bool = True) -> dict:
    ctx = {
        "session_id": "sess-1",
        "superstep": 0,
        "event_store": event_store,
        "grounding_scoring": {
            "enabled": True,
            "trigger": "always",
            "fallback": {"enabled": True, "kb_ids": [str(uuid.uuid4())]},
        },
    }
    return ctx


_CONTRADICTED_JUDGE = '[{"index": 1, "verdict": "contradicted", "confidence": 0.9}]'


class TestLLMWorkerChainWiring:
    async def test_chain_block_matches_legacy_canned_message(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="whatever")
        chains = build_llm_chain(pre_hook=None, post_hook=_BlockingPostHook())
        worker = LLMWorker(port=port, middleware_chains=chains)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context={"session_id": "sess-1", "superstep": 0, "event_store": event_store},
        )
        content = result.channel_updates["messages"][0]["content"]
        assert content == "I cannot provide that response due to safety policy."
        assert event_store.events == []

    async def test_chain_sanitize_modifies_response(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="raw response")
        chains = build_llm_chain(pre_hook=None, post_hook=_SanitizingPostHook())
        worker = LLMWorker(port=port, middleware_chains=chains)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context={"session_id": "sess-1", "superstep": 0, "event_store": event_store},
        )
        assert result.channel_updates["messages"][-1]["content"] == "sanitized content"

    async def test_legacy_hook_fallback_still_blocks(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="whatever")
        worker = LLMWorker(port=port, post_llm_hook=_BlockingPostHook())
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context={"session_id": "sess-1", "superstep": 0, "event_store": event_store},
        )
        assert (
            result.channel_updates["messages"][0]["content"] == "I cannot provide that response due to safety policy."
        )

    async def test_scoring_stage_appended_per_invocation_not_mutating_shared_chain(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="The platform was founded in 1998 by penguins.", judge_payload=_CONTRADICTED_JUDGE)
        chains = build_llm_chain(pre_hook=None, post_hook=None)
        base_stages = len(chains["llm_response"].stages)
        worker = LLMWorker(port=port, middleware_chains=chains)
        ctx = _scoring_ctx(event_store)
        for _ in range(2):
            await worker.execute(
                node_id="llm",
                node_config={"model": "gpt-4o"},
                channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
                execution_context=ctx,
            )
        assert len(chains["llm_response"].stages) == base_stages
        scores = event_store.by_name(EVENT_NAME_SCORE)
        assert len(scores) == 2

    async def test_contradicted_response_still_delivered(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="The platform was founded in 1998 by penguins.", judge_payload=_CONTRADICTED_JUDGE)
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context=_scoring_ctx(event_store),
        )
        assistant = result.channel_updates["messages"][-1]
        assert assistant["content"] == "The platform was founded in 1998 by penguins."
        scores = event_store.by_name(EVENT_NAME_SCORE)
        assert len(scores) == 1
        assert scores[0].payload["per_claim"][0]["verdict"] == "contradicted"
        assert scores[0].payload["would_block"]["triggered"] is True

    async def test_default_disabled_zero_behavior(self) -> None:
        event_store = StubEventStore()
        port = _make_port(response="The platform was founded in 1998 by penguins.")
        worker = LLMWorker(port=port)
        result = await worker.execute(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context={"session_id": "sess-1", "superstep": 0, "event_store": event_store},
        )
        assert event_store.events == []
        assert "grounding" not in result.channel_updates["messages"][-1]

    async def test_stream_path_scores_after_delivery(self) -> None:
        event_store = StubEventStore()

        async def fake_llm_invoke(messages, config):
            content = messages[-1]["content"] if messages else ""
            if JUDGE_PROMPT_TAG in content:
                yield _CONTRADICTED_JUDGE
            else:
                yield "The platform was founded in 1998 by penguins."

        port = MagicMock()
        port.context_assemble = AsyncMock(
            side_effect=lambda *a, **k: {"messages": k.get("messages", []), "tools": None, "metadata": {}}
        )
        port.llm_invoke = fake_llm_invoke
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)

        worker = LLMWorker(port=port)
        chunks = []
        async for item in worker.execute_stream(
            node_id="llm",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": [{"role": "user", "content": "Hi"}], "_session_id": "sess-1"},
            execution_context=_scoring_ctx(event_store),
        ):
            chunks.append(item)
        # Tokens were yielded (delivered) before the final result carrying the score.
        assert any(isinstance(c, dict) and c.get("content") for c in chunks)
        assert len(event_store.by_name(EVENT_NAME_SCORE)) == 1
