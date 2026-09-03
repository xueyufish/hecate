"""Tests for PresidioRecognizer.

presidio-analyzer is an optional dependency in the ``[security]`` extra.
The test environment does NOT have it installed, so we inject a fake
``presidio_analyzer`` module via ``sys.modules`` patching (using
real ``types.ModuleType`` instances because Python's import system
rejects MagicMock as a package) and flip the module-level
``_HAS_PRESIDIO`` flag.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import hecate.ops.dlp.recognizers.presidio as presidio_mod
from hecate.ops.dlp.recognizers.presidio import (
    _HAS_PRESIDIO,
    PresidioRecognizer,
)


class PresidioFake:
    """Mutable in-memory fake of presidio_analyzer.

    Exposes ``results`` to configure what ``analyze()`` returns,
    ``analyze_calls`` to inspect invocations, and ``raise_on_analyze``
    to exercise the resilience path.
    """

    def __init__(self) -> None:
        self.results: list[Any] = []
        self.analyze_calls: list[dict[str, Any]] = []
        self._raise: Exception | None = None

    def analyze(self, *, text: str, language: str) -> list[Any]:
        self.analyze_calls.append({"text": text, "language": language})
        if self._raise is not None:
            raise self._raise
        return list(self.results)


class _FakeResult:
    def __init__(self, entity_type: str | None, start: int, end: int, score: float) -> None:
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


@pytest.fixture
def fake_presidio(monkeypatch: pytest.MonkeyPatch) -> PresidioFake:
    fake = PresidioFake()

    fake_module = types.ModuleType("presidio_analyzer")

    class FakeAnalyzerEngine:
        def __init__(self) -> None:
            pass

        def analyze(self, *, text: str, language: str) -> list[Any]:
            return fake.analyze(text=text, language=language)

    fake_module.AnalyzerEngine = FakeAnalyzerEngine

    monkeypatch.setitem(sys.modules, "presidio_analyzer", fake_module)

    return fake


@pytest.fixture
def enable_presidio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presidio_mod, "_HAS_PRESIDIO", True)


class TestPresidioRecognizerMetadata:
    def test_name(self) -> None:
        assert PresidioRecognizer.name == "presidio"

    def test_supported_entities_contains_canonical(self) -> None:
        assert "EMAIL" in PresidioRecognizer.supported_entities
        assert "PHONE" in PresidioRecognizer.supported_entities
        assert "SSN" in PresidioRecognizer.supported_entities
        assert "CREDIT_CARD" in PresidioRecognizer.supported_entities


class TestPresidioRecognizerInit:
    def test_init_without_presidio_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(presidio_mod, "_HAS_PRESIDIO", False)
        with pytest.raises(ImportError, match="presidio-analyzer"):
            PresidioRecognizer()

    def test_init_with_presidio_succeeds(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        rec = PresidioRecognizer()
        assert rec is not None
        assert rec._language == "en"

    def test_custom_language(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        rec = PresidioRecognizer(language="zh")
        assert rec._language == "zh"


class TestPresidioRecognizerAnalyze:
    def test_analyze_empty_results(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        rec = PresidioRecognizer()
        assert rec.analyze("clean text") == []

    def test_analyze_maps_canonical_entity(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio.results = [
            _FakeResult("EMAIL_ADDRESS", 4, 20, 0.95),
        ]
        rec = PresidioRecognizer()
        findings = rec.analyze("see user@example.com today")
        assert len(findings) == 1
        assert findings[0].entity_type == "EMAIL"
        assert findings[0].value == "user@example.com"
        assert findings[0].start == 4
        assert findings[0].end == 20
        assert findings[0].score == 0.95
        assert findings[0].recognizer == "presidio"

    def test_analyze_passes_through_unknown_entity(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio.results = [
            _FakeResult("US_DRIVER_LICENSE", 0, 8, 0.7),
        ]
        rec = PresidioRecognizer()
        findings = rec.analyze("A1234567 here")
        assert len(findings) == 1
        assert findings[0].entity_type == "US_DRIVER_LICENSE"

    def test_analyze_filters_by_entities(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio.results = [
            _FakeResult("EMAIL_ADDRESS", 0, 16, 0.95),
            _FakeResult("PHONE_NUMBER", 20, 33, 0.85),
            _FakeResult("PERSON", 40, 47, 0.7),
        ]
        rec = PresidioRecognizer()
        findings = rec.analyze("user@example.com 555-123-4567 Bob Smith", entities=["EMAIL"])
        assert len(findings) == 1
        assert findings[0].entity_type == "EMAIL"

    def test_analyze_multiple_findings(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio.results = [
            _FakeResult("EMAIL_ADDRESS", 0, 16, 0.95),
            _FakeResult("PHONE_NUMBER", 20, 33, 0.85),
        ]
        rec = PresidioRecognizer()
        findings = rec.analyze("user@example.com 555-123-4567")
        assert len(findings) == 2
        types = {f.entity_type for f in findings}
        assert types == {"EMAIL", "PHONE"}

    def test_analyze_continues_when_engine_raises(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio._raise = RuntimeError("engine down")
        rec = PresidioRecognizer()
        assert rec.analyze("text") == []

    def test_analyze_skips_result_without_entity_type(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio.results = [
            _FakeResult(None, 0, 4, 0.9),
            _FakeResult("EMAIL_ADDRESS", 10, 26, 0.95),
        ]
        rec = PresidioRecognizer()
        findings = rec.analyze("blah see user@example.com today")
        assert len(findings) == 1
        assert findings[0].entity_type == "EMAIL"

    def test_analyze_skips_result_with_invalid_positions(
        self, enable_presidio: None, fake_presidio: PresidioFake
    ) -> None:
        class BadResult:
            entity_type = "EMAIL_ADDRESS"
            start = "not-a-number"
            end = 10
            score = 0.5

        fake_presidio.results = [BadResult()]
        rec = PresidioRecognizer()
        assert rec.analyze("anything") == []

    def test_analyze_default_score_when_none(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        fake_presidio.results = [_FakeResult("EMAIL_ADDRESS", 0, 16, None)]
        rec = PresidioRecognizer()
        findings = rec.analyze("user@example.com here")
        assert len(findings) == 1
        assert findings[0].score == 0.5

    def test_analyze_passes_language_to_engine(self, enable_presidio: None, fake_presidio: PresidioFake) -> None:
        rec = PresidioRecognizer(language="zh")
        rec.analyze("text")
        assert fake_presidio.analyze_calls[-1]["language"] == "zh"


class TestPresidioRecognizerDependencyDetection:
    def test_module_flag_reflects_actual_import(self) -> None:
        assert isinstance(_HAS_PRESIDIO, bool)
