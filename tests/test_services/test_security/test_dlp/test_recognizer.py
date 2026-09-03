"""Tests for DLPRecognizer ABC and DLPRecognizerRegistry.

Covers spec §dlp-scanner ADDED Requirements for the recognizer layer:
* DLPRecognizer cannot be instantiated directly.
* Concrete subclasses that implement analyze() can be instantiated.
* Registry runs all registered recognizers and merges results.
* Registry deduplicates overlapping findings by score.
* Registry filters by entity type when caller supplies a whitelist.
"""

from __future__ import annotations

import pytest

from hecate.ops.dlp.recognizer import (
    DLPRecognizer,
    DLPRecognizerRegistry,
)
from hecate.ops.dlp.result import DLPFinding


class _StubRecognizer(DLPRecognizer):
    """Test double that returns a fixed list of findings."""

    def __init__(
        self,
        name: str,
        entities: list[str],
        findings: list[DLPFinding],
    ) -> None:
        self.name = name
        self.supported_entities = entities
        self._findings = findings

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        if entities is None:
            return list(self._findings)
        return [f for f in self._findings if f.entity_type in entities]


class TestDLPRecognizerABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            DLPRecognizer()

    def test_subclass_with_implementation_succeeds(self) -> None:
        rec = _StubRecognizer("stub", ["EMAIL"], [])
        assert rec.name == "stub"
        assert rec.supported_entities == ["EMAIL"]

    def test_subclass_without_analyze_raises(self) -> None:
        class IncompleteRecognizer(DLPRecognizer):
            name = "incomplete"
            supported_entities: list[str] = []

        with pytest.raises(TypeError):
            IncompleteRecognizer()


class TestDLPRecognizerRegistry:
    def test_register_and_unregister(self) -> None:
        registry = DLPRecognizerRegistry()
        rec = _StubRecognizer("stub", ["EMAIL"], [])
        registry.register(rec)
        assert registry.get("stub") is rec
        assert "stub" in registry.names()

        registry.unregister("stub")
        assert registry.get("stub") is None
        assert "stub" not in registry.names()

    def test_unregister_unknown_is_noop(self) -> None:
        registry = DLPRecognizerRegistry()
        registry.unregister("never-registered")  # must not raise

    def test_register_empty_name_raises(self) -> None:
        class NamelessRecognizer(DLPRecognizer):
            name = ""
            supported_entities: list[str] = []

            def analyze(
                self,
                text: str,
                entities: list[str] | None = None,
            ) -> list[DLPFinding]:
                return []

        registry = DLPRecognizerRegistry()
        with pytest.raises(ValueError, match="non-empty name"):
            registry.register(NamelessRecognizer())

    def test_runs_all_registered_recognizers(self) -> None:
        f1 = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=1.0,
            recognizer="r1",
        )
        f2 = DLPFinding(
            entity_type="PHONE",
            value="555-1234",
            start=10,
            end=18,
            score=1.0,
            recognizer="r2",
        )
        f3 = DLPFinding(
            entity_type="SSN",
            value="123-45-6789",
            start=20,
            end=31,
            score=1.0,
            recognizer="r3",
        )
        registry = DLPRecognizerRegistry()
        registry.register(_StubRecognizer("r1", ["EMAIL"], [f1]))
        registry.register(_StubRecognizer("r2", ["PHONE"], [f2]))
        registry.register(_StubRecognizer("r3", ["SSN"], [f3]))

        results = registry.analyze("ignored")
        assert len(results) == 3
        entity_types = {r.entity_type for r in results}
        assert entity_types == {"EMAIL", "PHONE", "SSN"}

    def test_deduplicates_overlapping_findings_keeps_higher_score(self) -> None:
        f_low = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=0.5,
            recognizer="low_quality",
        )
        f_high = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=0.95,
            recognizer="high_quality",
        )
        registry = DLPRecognizerRegistry()
        registry.register(_StubRecognizer("a", ["EMAIL"], [f_low]))
        registry.register(_StubRecognizer("b", ["EMAIL"], [f_high]))

        results = registry.analyze("a@b.com")
        assert len(results) == 1
        assert results[0].score == 0.95
        assert results[0].recognizer == "high_quality"

    def test_deduplicates_overlapping_keeps_first_on_tie(self) -> None:
        f_first = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=0.9,
            recognizer="first",
        )
        f_second = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=0.9,
            recognizer="second",
        )
        registry = DLPRecognizerRegistry()
        registry.register(_StubRecognizer("first", ["EMAIL"], [f_first]))
        registry.register(_StubRecognizer("second", ["EMAIL"], [f_second]))

        results = registry.analyze("a@b.com")
        assert len(results) == 1
        assert results[0].recognizer == "first"

    def test_keeps_non_overlapping_findings(self) -> None:
        f1 = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=1.0,
            recognizer="r1",
        )
        f2 = DLPFinding(
            entity_type="PHONE",
            value="555-1234",
            start=10,
            end=18,
            score=1.0,
            recognizer="r2",
        )
        registry = DLPRecognizerRegistry()
        registry.register(_StubRecognizer("r1", ["EMAIL"], [f1]))
        registry.register(_StubRecognizer("r2", ["PHONE"], [f2]))

        results = registry.analyze("text")
        assert len(results) == 2

    def test_filter_by_entity_types(self) -> None:
        f_email = DLPFinding(
            entity_type="EMAIL",
            value="a@b.com",
            start=0,
            end=7,
            score=1.0,
            recognizer="r1",
        )
        f_phone = DLPFinding(
            entity_type="PHONE",
            value="555-1234",
            start=10,
            end=18,
            score=1.0,
            recognizer="r1",
        )
        registry = DLPRecognizerRegistry()
        registry.register(_StubRecognizer("multi", ["EMAIL", "PHONE"], [f_email, f_phone]))

        results = registry.analyze("text", entities=["EMAIL"])
        assert len(results) == 1
        assert results[0].entity_type == "EMAIL"

    def test_empty_registry_returns_no_findings(self) -> None:
        registry = DLPRecognizerRegistry()
        assert registry.analyze("anything") == []
