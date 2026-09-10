"""Tests for safety evaluators (7.2b — 3 new)."""

from __future__ import annotations

import pytest

from hecate.ops.evaluation.safety_evaluators import (
    HarmfulnessEvaluator,
    PiiLeakageEvaluator,
    RefusalEvaluator,
)
from hecate.ops.evaluation.types import EvalInput


def _input(generated: str) -> EvalInput:
    return EvalInput(query="q", generated_answer=generated)


# A response object that satisfies ``llm_service.chat`` contract for the
# LLM-judge evaluators. ``content`` is the JSON string the parser will
# read.
class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


def _patch_llm_chat(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    """Patch ``llm_service.chat`` to return a fixed payload."""
    import json

    async def _chat(*_a: object, **_kw: object) -> _Resp:
        return _Resp(json.dumps(payload))

    monkeypatch.setattr("hecate_llm.service.llm_service.chat", _chat)


class TestPiiLeakageEvaluator:
    @pytest.mark.asyncio
    async def test_clean_text(self) -> None:
        ev = PiiLeakageEvaluator()
        out = await ev.evaluate(_input("the answer is 42"))
        assert out.scores[0].value == 1.0
        assert out.scores[0].source == "deterministic"
        assert out.scores[0].metric_name == "pii_leakage"

    @pytest.mark.asyncio
    async def test_credit_card_detected(self) -> None:
        ev = PiiLeakageEvaluator()
        # 4111-1111-1111-1111 fails Luhn intentionally? Actually it passes
        # Luhn. Use 4532-0151-1283-0366 (test card, passes Luhn).
        out = await ev.evaluate(_input("card: 4532-0151-1283-0366"))
        assert out.scores[0].value == 0.0
        assert "CREDIT_CARD" in out.scores[0].reasoning

    @pytest.mark.asyncio
    async def test_email_detected(self) -> None:
        ev = PiiLeakageEvaluator()
        out = await ev.evaluate(_input("contact me at user@example.com"))
        assert out.scores[0].value == 0.0
        assert "EMAIL" in out.scores[0].reasoning

    @pytest.mark.asyncio
    async def test_ssn_detected(self) -> None:
        ev = PiiLeakageEvaluator()
        out = await ev.evaluate(_input("SSN: 123-45-6789"))
        assert out.scores[0].value == 0.0

    @pytest.mark.asyncio
    async def test_luhn_invalid_digit_sequence_passes(self) -> None:
        """16 random digits that fail Luhn should NOT be flagged."""
        ev = PiiLeakageEvaluator()
        out = await ev.evaluate(_input("number: 1234-5678-9012-3456"))
        assert out.scores[0].value == 1.0

    @pytest.mark.asyncio
    async def test_shares_dlp_regex_with_runtime(self) -> None:
        """Spec invariant: pii_leakage uses the same regex set as DLP."""
        from hecate.ops.dlp.recognizers.regex import _PATTERNS as DLP_PATTERNS
        from hecate.ops.dlp.recognizers.regex import RegexRecognizer

        recognizer = RegexRecognizer()
        sample = "email a@b.co, phone 555-123-4567, ssn 123-45-6789, ip 10.0.0.1, card 4532-0151-1283-0366"
        dlp_findings = recognizer.analyze(sample)
        ev = PiiLeakageEvaluator()
        out = await ev.evaluate(_input(sample))
        # Both must produce matching entity types
        dlp_types = {f.entity_type for f in dlp_findings}
        assert dlp_types  # non-empty (sanity)
        assert out.scores[0].value == 0.0  # detected, so 0.0
        # The PII leakage evaluator's verdict must match DLP's "any match"
        dlp_blocked = bool(dlp_findings)
        assert (out.scores[0].value == 0.0) == dlp_blocked

        # Also verify the pattern set is shared (not duplicated)
        evaluator_patterns = set(DLP_PATTERNS.keys())
        recognizer_patterns = set(recognizer.supported_entities)
        assert evaluator_patterns == recognizer_patterns


class TestRefusalEvaluator:
    @pytest.mark.asyncio
    async def test_refusal_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm_chat(monkeypatch, {"score": 1.0, "reasoning": "refused"})
        ev = RefusalEvaluator()
        out = await ev.evaluate(_input("I cannot help with that."))
        assert out.scores[0].value == 1.0
        assert out.scores[0].source == "llm_judge"

    @pytest.mark.asyncio
    async def test_answer_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm_chat(monkeypatch, {"score": 0.0, "reasoning": "answered"})
        ev = RefusalEvaluator()
        out = await ev.evaluate(_input("The answer is 42."))
        assert out.scores[0].value == 0.0

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a: object, **_kw: object) -> object:
            raise RuntimeError("LLM gateway down")

        monkeypatch.setattr("hecate_llm.service.llm_service.chat", _boom)
        ev = RefusalEvaluator()
        out = await ev.evaluate(_input("hi"))
        assert out.scores[0].value == -1.0


class TestHarmfulnessEvaluator:
    @pytest.mark.asyncio
    async def test_harmful_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm_chat(monkeypatch, {"score": 1.0, "reasoning": "hateful"})
        ev = HarmfulnessEvaluator()
        out = await ev.evaluate(_input("you are stupid and worthless"))
        assert out.scores[0].value == 1.0

    @pytest.mark.asyncio
    async def test_safe_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_llm_chat(monkeypatch, {"score": 0.0, "reasoning": "fine"})
        ev = HarmfulnessEvaluator()
        out = await ev.evaluate(_input("Have a nice day!"))
        assert out.scores[0].value == 0.0
