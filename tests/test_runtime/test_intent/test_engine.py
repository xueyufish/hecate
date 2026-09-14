"""Tests for the intent recognition engine (6.23 ⊕ 1.3.10)."""

from __future__ import annotations

import json
import uuid

import pytest

from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.intent.engine import IntentConfig, IntentRecognitionEngine
from hecate.runtime.intent.events import emit_intent_recognized
from hecate.runtime.intent.types import (
    DecisionSource,
    EvidenceCategory,
    EvidencePayload,
    IntentEvidencePort,
    IntentRecognizer,
    IntentRequest,
)


class StubLLMPort:
    """RuntimePort-like stub returning canned LLM JSON, counting calls."""

    def __init__(self, response: str, last_config_holder: dict | None = None) -> None:
        self.response = response
        self.calls = 0
        self.last_messages: list[dict] = []
        self.last_config: dict = {}
        self.holder = last_config_holder or {}

    async def llm_invoke(self, messages: list[dict], config: dict):
        self.calls += 1
        self.last_messages = messages
        self.last_config = config
        yield self.response


class StubEvidencePort(IntentEvidencePort):
    """Published-evidence provider stub; raises when told to fail."""

    def __init__(self, payload: EvidencePayload | None, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail

    async def get_evidence(self, package_id, version_id=None, workspace_id=None) -> EvidencePayload:
        if self.fail or self.payload is None:
            raise LookupError("no published version")
        return self.payload


def _evidence(version: uuid.UUID | None = None, gated: bool = False) -> EvidencePayload:
    return EvidencePayload(
        package_id=uuid.uuid4(),
        version_id=version or uuid.uuid4(),
        version_name="v1",
        categories=(
            EvidenceCategory(
                name="billing",
                description="Billing and invoices",
                domain="finance",
                policy_gated=gated,
                samples=("refund my order", "invoice copy", "billing question"),
            ),
            EvidenceCategory(name="tech", description="Technical issues", samples=("app crashes",)),
        ),
    )


def _request(utterance: str, evidence=None, fallback_labels=(), session_intent=None, model=None):
    return IntentRequest(
        utterance=utterance,
        evidence=evidence,
        fallback_labels=fallback_labels,
        session_intent=session_intent or {},
        recognition_model=model,
    )


# ---------------------------------------------------------------------------
# Interface contract
# ---------------------------------------------------------------------------


def test_recognizer_abc_not_instantiable():
    with pytest.raises(TypeError):
        IntentRecognizer()  # type: ignore[abstract]


def test_evidence_port_abc_not_instantiable():
    with pytest.raises(TypeError):
        IntentEvidencePort()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# L1 pipeline
# ---------------------------------------------------------------------------


async def test_pattern_match_skips_llm():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    evidence = EvidencePayload(
        package_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_name="v1",
        categories=(
            EvidenceCategory(
                name="billing",
                description="Billing",
                domain="finance",
                policy_gated=False,
                patterns=("invoice|billing",),
                samples=("refund my order",),
            ),
        ),
    )
    engine = IntentRecognitionEngine(port=port)
    result = await engine.recognize(_request("I have an invoice problem", evidence=evidence))
    assert result.atomic.label == "billing"
    assert result.atomic.source == DecisionSource.PATTERN
    assert port.calls == 0
    assert result.domain == "finance"


async def test_few_shot_llm_classification():
    port = StubLLMPort('{"label": "billing", "confidence": 0.87, "goal_shift": false}')
    engine = IntentRecognitionEngine(port=port)
    result = await engine.recognize(_request("where is my refund", evidence=_evidence()))
    assert result.atomic.label == "billing"
    assert result.atomic.source == DecisionSource.FEW_SHOT
    assert result.atomic.confidence == pytest.approx(0.87)
    assert result.domain == "finance"
    assert port.calls == 1
    # Few-shot evidence carries sample utterances and the label constraint.
    system = port.last_messages[0]["content"]
    assert "refund my order" in system
    assert "Never invent a label" in system


async def test_llm_invalid_label_falls_back():
    port = StubLLMPort('{"label": "made_up_label", "confidence": 0.99}')
    engine = IntentRecognitionEngine(port=port)
    result = await engine.recognize(_request("something odd", evidence=_evidence()))
    assert result.atomic.label is None
    assert result.atomic.source == DecisionSource.FALLBACK


async def test_recognition_model_config_passed_separately():
    port = StubLLMPort('{"label": "tech", "confidence": 0.8}')
    engine = IntentRecognitionEngine(port=port)
    await engine.recognize(_request("app crashes", evidence=_evidence(), model="small-fast-model"))
    assert port.last_config.get("model") == "small-fast-model"


async def test_evidence_failure_degrades_to_fallback_labels():
    port = StubLLMPort('{"label": "billing", "confidence": 0.7}')
    engine = IntentRecognitionEngine(port=port)
    result = await engine.recognize(_request("refund question", evidence=None, fallback_labels=("billing", "tech")))
    assert result.atomic.label == "billing"
    assert result.atomic.source == DecisionSource.LLM
    assert result.evidence_available is False


# ---------------------------------------------------------------------------
# Decision cache
# ---------------------------------------------------------------------------


async def test_cache_hit_skips_llm():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    evidence = _evidence()
    engine = IntentRecognitionEngine(port=port)
    first = await engine.recognize(_request("refund my order", evidence=evidence))
    assert first.atomic.source == DecisionSource.FEW_SHOT
    second = await engine.recognize(_request("REFUND   my order", evidence=evidence))
    assert second.atomic.source == DecisionSource.CACHE
    assert second.atomic.label == "billing"
    assert second.cache_hit is True
    assert port.calls == 1  # no second LLM call


async def test_cache_misses_on_evidence_version_change():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    engine = IntentRecognitionEngine(port=port)
    await engine.recognize(_request("refund my order", evidence=_evidence()))
    await engine.recognize(_request("refund my order", evidence=_evidence()))  # new version id
    assert port.calls == 2


async def test_cache_misses_on_context_change():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    evidence = _evidence()
    engine = IntentRecognitionEngine(port=port)
    await engine.recognize(_request("refund my order", evidence=evidence, session_intent={"goal": None}))
    await engine.recognize(_request("refund my order", evidence=evidence, session_intent={"goal": "tech"}))
    assert port.calls == 2


async def test_fallback_decision_not_cached():
    port = StubLLMPort("garbage, not json")
    evidence = _evidence()
    engine = IntentRecognitionEngine(port=port)
    await engine.recognize(_request("weird input", evidence=evidence))
    await engine.recognize(_request("weird input", evidence=evidence))
    assert port.calls == 2  # fallback never pins the failure


# ---------------------------------------------------------------------------
# L2 workflow / L3 session intent
# ---------------------------------------------------------------------------


async def test_workflow_activates_on_consistent_window():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    engine = IntentRecognitionEngine(port=port)
    evidence = _evidence()
    state = {"goal": "billing", "turn_labels": ["billing"], "workflow_label": None}
    first = await engine.recognize(_request("refund order", evidence=evidence, session_intent=state))
    second = await engine.recognize(
        _request("invoice copy", evidence=evidence, session_intent=first.session_intent_update)
    )
    assert second.workflow.active is True
    assert second.workflow.label == "billing"


async def test_multi_step_cue_activates_workflow():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    engine = IntentRecognitionEngine(port=port)
    result = await engine.recognize(_request("generate the report and then send it", evidence=_evidence()))
    assert result.workflow.active is True


async def test_session_goal_sticky_without_shift():
    port = StubLLMPort('{"label": "tech", "confidence": 0.8, "goal_shift": false}')
    engine = IntentRecognitionEngine(port=port)
    state = {"goal": "billing", "turn_labels": ["billing"], "workflow_label": None}
    result = await engine.recognize(_request("app crashes", evidence=_evidence(), session_intent=state))
    assert result.session_intent_update["goal"] == "billing"  # sticky
    assert result.goal_shift is False


async def test_explicit_shift_cue_replaces_goal():
    port = StubLLMPort('{"label": "tech", "confidence": 0.9, "goal_shift": false}')
    engine = IntentRecognitionEngine(port=port)
    state = {"goal": "billing", "turn_labels": ["billing"], "workflow_label": None}
    result = await engine.recognize(
        _request("actually, switch to fixing the app", evidence=_evidence(), session_intent=state)
    )
    assert result.session_intent_update["goal"] == "tech"
    assert result.goal_shift is True


async def test_llm_shift_verdict_replaces_goal():
    port = StubLLMPort('{"label": "tech", "confidence": 0.9, "goal_shift": true}')
    engine = IntentRecognitionEngine(port=port)
    state = {"goal": "billing", "turn_labels": ["billing"], "workflow_label": None}
    result = await engine.recognize(_request("fix the app now", evidence=_evidence(), session_intent=state))
    assert result.session_intent_update["goal"] == "tech"
    assert result.goal_shift is True


async def test_persistent_mismatch_drifts_goal():
    port = StubLLMPort('{"label": "tech", "confidence": 0.8, "goal_shift": false}')
    engine = IntentRecognitionEngine(port=port, config=IntentConfig(shift_streak_threshold=2))
    state = {"goal": "billing", "turn_labels": ["billing", "tech"], "workflow_label": None}
    result = await engine.recognize(_request("another tech thing", evidence=_evidence(), session_intent=state))
    assert result.session_intent_update["goal"] == "tech"


# ---------------------------------------------------------------------------
# L4/L5
# ---------------------------------------------------------------------------


async def test_policy_gated_category_marks_result():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    engine = IntentRecognitionEngine(port=port)
    result = await engine.recognize(_request("refund question", evidence=_evidence(gated=True)))
    assert result.gated is True
    assert result.atomic.gated is True


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def test_intent_recognized_event_carries_metadata_not_payloads():
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    engine = IntentRecognitionEngine(port=port)
    evidence = _evidence()
    result = await engine.recognize(_request("refund my order", evidence=evidence))
    await emit_intent_recognized(store, session_id, result)

    events = await store.get_events(session_id)
    assert len(events) == 1
    assert events[0].event_type == EventType.INTENT_RECOGNIZED
    payload = events[0].payload
    assert payload["atomic"]["label"] == "billing"
    assert payload["cache_hit"] is False
    assert payload["evidence_available"] is True
    # The utterance itself and the evidence payloads never enter the event.
    assert "refund my order" not in json.dumps(payload)
    assert "invoice copy" not in json.dumps(payload)
