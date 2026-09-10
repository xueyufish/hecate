"""Tests for deterministic format evaluators (7.2b — 4 new)."""

from __future__ import annotations

import pytest

from hecate.ops.evaluation.format_evaluators import (
    ContainsEvaluator,
    ExactMatchEvaluator,
    IsJsonEvaluator,
    RegexMatchEvaluator,
)
from hecate.ops.evaluation.types import EvalInput


def _input(generated: str, expected: str | None = None, pattern: str | None = None) -> EvalInput:
    md = {"pattern": pattern} if pattern else {}
    return EvalInput(
        query="q",
        generated_answer=generated,
        expected_answer=expected,
        metadata=md,
    )


class TestContainsEvaluator:
    @pytest.mark.asyncio
    async def test_substring_present(self) -> None:
        ev = ContainsEvaluator()
        out = await ev.evaluate(_input("RAG stands for Retrieval Augmented Generation", "Retrieval"))
        assert out.scores[0].value == 1.0
        assert out.scores[0].source == "deterministic"
        assert out.scores[0].metric_name == "contains"

    @pytest.mark.asyncio
    async def test_substring_absent(self) -> None:
        ev = ContainsEvaluator()
        out = await ev.evaluate(_input("hello world", "missing"))
        assert out.scores[0].value == 0.0

    @pytest.mark.asyncio
    async def test_pattern_from_metadata(self) -> None:
        ev = ContainsEvaluator()
        out = await ev.evaluate(_input("abc-123-xyz", pattern="xyz"))
        assert out.scores[0].value == 1.0

    @pytest.mark.asyncio
    async def test_missing_needle(self) -> None:
        ev = ContainsEvaluator()
        out = await ev.evaluate(_input("hello"))
        assert out.scores[0].value == 0.0
        assert "expected_answer" in out.scores[0].reasoning


class TestExactMatchEvaluator:
    @pytest.mark.asyncio
    async def test_match(self) -> None:
        ev = ExactMatchEvaluator()
        out = await ev.evaluate(_input("Paris", "Paris"))
        assert out.scores[0].value == 1.0

    @pytest.mark.asyncio
    async def test_mismatch(self) -> None:
        ev = ExactMatchEvaluator()
        out = await ev.evaluate(_input("Paris", "London"))
        assert out.scores[0].value == 0.0

    @pytest.mark.asyncio
    async def test_strip_whitespace(self) -> None:
        ev = ExactMatchEvaluator()
        out = await ev.evaluate(_input("  Paris  ", "Paris"))
        assert out.scores[0].value == 1.0


class TestIsJsonEvaluator:
    @pytest.mark.asyncio
    async def test_valid_json(self) -> None:
        ev = IsJsonEvaluator()
        out = await ev.evaluate(_input('{"key": "value"}'))
        assert out.scores[0].value == 1.0

    @pytest.mark.asyncio
    async def test_invalid_json(self) -> None:
        ev = IsJsonEvaluator()
        out = await ev.evaluate(_input("not json"))
        assert out.scores[0].value == 0.0

    @pytest.mark.asyncio
    async def test_empty_string(self) -> None:
        ev = IsJsonEvaluator()
        out = await ev.evaluate(_input(""))
        assert out.scores[0].value == 0.0


class TestRegexMatchEvaluator:
    @pytest.mark.asyncio
    async def test_pattern_matches(self) -> None:
        ev = RegexMatchEvaluator()
        out = await ev.evaluate(_input("Error code: E-1234", pattern=r"E-\d{4}"))
        assert out.scores[0].value == 1.0

    @pytest.mark.asyncio
    async def test_pattern_no_match(self) -> None:
        ev = RegexMatchEvaluator()
        out = await ev.evaluate(_input("hello", pattern=r"E-\d{4}"))
        assert out.scores[0].value == 0.0

    @pytest.mark.asyncio
    async def test_missing_pattern(self) -> None:
        ev = RegexMatchEvaluator()
        out = await ev.evaluate(_input("hello"))
        assert out.scores[0].value == 0.0
        assert "metadata.pattern" in out.scores[0].reasoning

    @pytest.mark.asyncio
    async def test_invalid_regex(self) -> None:
        ev = RegexMatchEvaluator()
        out = await ev.evaluate(_input("hello", pattern="[unclosed"))
        assert out.scores[0].value == 0.0
        assert "Invalid regex" in out.scores[0].reasoning


class TestDeterministicEvaluatorsNoLLM:
    """Spec contract: deterministic evaluators never invoke the LLM gateway."""

    @pytest.mark.asyncio
    async def test_contains_no_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hecate.ops.evaluation.format_evaluators import ContainsEvaluator

        called = {"n": 0}

        async def _spy(*_a: object, **_kw: object) -> object:
            called["n"] += 1
            return None

        monkeypatch.setattr("hecate_llm.service.llm_service.chat", _spy)
        ev = ContainsEvaluator()
        await ev.evaluate(_input("hello", "ell"))
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_pii_leakage_no_llm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hecate.ops.evaluation.safety_evaluators import PiiLeakageEvaluator

        called = {"n": 0}

        async def _spy(*_a: object, **_kw: object) -> object:
            called["n"] += 1
            return None

        monkeypatch.setattr("hecate_llm.service.llm_service.chat", _spy)
        ev = PiiLeakageEvaluator()
        await ev.evaluate(_input("clean text"))
        assert called["n"] == 0
