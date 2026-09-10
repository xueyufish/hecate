"""Tests for synthesis filter pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from hecate.ops.evaluation.synthesis import CandidateItem
from hecate.ops.evaluation.synthesis.filters import (
    DLPFilter,
    EmbeddingDedupeFilter,
    QualityFilter,
    _jaccard,
    _ngrams,
)


@dataclass
class _FakeFinding:
    entity_type: str


@dataclass
class _FakeDLPResult:
    findings: list = field(default_factory=list)


class TestNgramHelpers:
    def test_ngrams_short(self) -> None:
        assert _ngrams("ab", 3) == {"ab"}

    def test_ngrams_normal(self) -> None:
        assert _ngrams("abcd", 2) == {"ab", "bc", "cd"}

    def test_jaccard_disjoint(self) -> None:
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_identical(self) -> None:
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_jaccard_overlap(self) -> None:
        assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 2 / 4


class TestEmbeddingDedupeFilter:
    @pytest.mark.asyncio
    async def test_empty_input(self) -> None:
        f = EmbeddingDedupeFilter()
        result = await f.filter([])
        assert result.passed == []
        assert result.dropped == []

    @pytest.mark.asyncio
    async def test_empty_dataset_passes_all(self) -> None:
        f = EmbeddingDedupeFilter()
        items = [CandidateItem(query="hello world", strategy="generation")]
        result = await f.filter(items, target_dataset_id="any")
        assert result.passed == items
        assert result.dropped == []

    @pytest.mark.asyncio
    async def test_mock_embedding_falls_back_to_ngram(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When embedding service is None (mock mode), filter uses ngram."""
        from hecate.ops.evaluation.synthesis import filters as filt_mod

        class _MockService:
            _model = "mock"

        existing_query = "the quick brown fox jumps over the lazy dog today"

        async def _loader(self, target: str | None) -> list[str]:  # noqa: ARG001
            return [existing_query]

        # Bind the loader via the class so instance lookup resolves it.
        monkeypatch.setattr(filt_mod.EmbeddingDedupeFilter, "_load_existing_queries", _loader)
        f = EmbeddingDedupeFilter(embedding_service=_MockService())
        # Candidate differs by a single trailing word — trigram Jaccard > 0.95.
        near_duplicate = "the quick brown fox jumps over the lazy dog"
        result = await f.filter(
            [CandidateItem(query=near_duplicate, strategy="generation")],
            target_dataset_id="any",
        )
        assert len(result.dropped) == 1
        assert "ngram_jacc" in result.dropped[0][1]

    @pytest.mark.asyncio
    async def test_mock_embedding_dissimilar_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hecate.ops.evaluation.synthesis import filters as filt_mod

        class _MockService:
            _model = "mock"

        async def _loader(self, target: str | None) -> list[str]:  # noqa: ARG001
            return ["hello world"]

        monkeypatch.setattr(filt_mod.EmbeddingDedupeFilter, "_load_existing_queries", _loader)
        f = EmbeddingDedupeFilter(embedding_service=_MockService())
        result = await f.filter(
            [CandidateItem(query="completely different topic about cats", strategy="generation")],
            target_dataset_id="any",
        )
        assert len(result.passed) == 1
        assert result.dropped == []


def _fake_loader(queries: list[str]):
    async def _loader(self, target: str | None) -> list[str]:  # noqa: ARG001
        return queries

    return _loader


class TestQualityFilter:
    @pytest.mark.asyncio
    async def test_high_scores_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_quality(monkeypatch, containment=0.9, clarity=0.9)
        f = QualityFilter(threshold=0.6)
        result = await f.filter([CandidateItem(query="q", strategy="generation")])
        assert len(result.passed) == 1
        assert result.dropped == []

    @pytest.mark.asyncio
    async def test_low_containment_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_quality(monkeypatch, containment=0.3, clarity=0.9)
        f = QualityFilter(threshold=0.6)
        result = await f.filter([CandidateItem(query="q", strategy="generation")])
        assert len(result.dropped) == 1
        assert "containment" in result.dropped[0][1]

    @pytest.mark.asyncio
    async def test_low_clarity_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_quality(monkeypatch, containment=0.9, clarity=0.4)
        f = QualityFilter(threshold=0.6)
        result = await f.filter([CandidateItem(query="q", strategy="generation")])
        assert len(result.dropped) == 1
        assert "clarity" in result.dropped[0][1]

    @pytest.mark.asyncio
    async def test_critic_failure_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a: object, **_kw: object) -> object:
            raise RuntimeError("critic down")

        monkeypatch.setattr("hecate_llm.service.llm_service.chat", _boom)
        f = QualityFilter(threshold=0.6)
        result = await f.filter([CandidateItem(query="q", strategy="generation")])
        # fail-lenient: passes on infrastructure error
        assert len(result.passed) == 1


def _patch_quality(monkeypatch: pytest.MonkeyPatch, *, containment: float, clarity: float) -> None:
    async def _chat(*_a: object, **_kw: object) -> object:
        class _R:
            content = json.dumps({"self_containment": containment, "clarity": clarity})

        return _R

    monkeypatch.setattr("hecate_llm.service.llm_service.chat", _chat)


class TestDLPFilter:
    @pytest.mark.asyncio
    async def test_no_db_passes_all(self) -> None:
        f = DLPFilter(db=None)
        result = await f.filter([CandidateItem(query="q", strategy="generation")])
        assert len(result.passed) == 1

    @pytest.mark.asyncio
    async def test_dlp_finding_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The filters module imports ``DLPService`` inside ``filter`` —
        # monkeypatch ``hecate.ops.dlp.service.DLPService`` at the source.
        from hecate.ops.dlp import service as dlp_service_mod

        class _StubDLPService:
            def __init__(self, *_a: object, **_kw: object) -> None:
                pass

            async def dry_run_scan(self, *_a: object, **_kw: object) -> _FakeDLPResult:
                return _FakeDLPResult(findings=[_FakeFinding("EMAIL")])

        monkeypatch.setattr(dlp_service_mod, "DLPService", _StubDLPService)
        result = await DLPFilter(db=object()).filter(  # type: ignore[arg-type]
            [CandidateItem(query="q", strategy="generation")]
        )
        assert len(result.dropped) == 1
        assert result.dropped[0][1] == "dlp_blocked"

    @pytest.mark.asyncio
    async def test_dlp_clean_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hecate.ops.dlp import service as dlp_service_mod

        class _StubDLPService:
            def __init__(self, *_a: object, **_kw: object) -> None:
                pass

            async def dry_run_scan(self, *_a: object, **_kw: object) -> _FakeDLPResult:
                return _FakeDLPResult(findings=[])

        monkeypatch.setattr(dlp_service_mod, "DLPService", _StubDLPService)
        result = await DLPFilter(db=object()).filter(  # type: ignore[arg-type]
            [CandidateItem(query="q", strategy="generation")]
        )
        assert len(result.passed) == 1


@dataclass
class _FakeDLPService:
    findings: list = field(default_factory=list)

    async def dry_run_scan(self, *_a: object, **_kw: object) -> _FakeDLPResult:
        return _FakeDLPResult(findings=self.findings)
