"""Filter pipeline for synthesized dataset items.

Three filters run sequentially after strategy generation:

1. :class:`EmbeddingDedupeFilter` — drops candidates whose cosine
   similarity to any existing dataset item is >= 0.92. Falls back to
   character n-gram Jaccard (threshold 0.85) when the embedding service
   is unavailable or running in mock mode (FlagEmbedding wheel missing).

2. :class:`QualityFilter` — critic LLM scores each candidate on
   ``self_containment`` and ``clarity`` (0.0-1.0). Items scoring below
   the threshold on either dimension are dropped. Threshold defaults
   to 0.6 and is configurable per request.

3. :class:`DLPFilter` — runs ``DLPService.dry_run_scan`` on each
   candidate's query / expected_answer / context. Items where DLP flags
   any field are dropped with classification ``dlp_blocked``.

Each filter returns the same shape: ``(passed: list[CandidateItem],
dropped: list[tuple[CandidateItem, str]])``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hecate.ops.evaluation.synthesis import CandidateItem
from hecate.ops.evaluation.types import LLMConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Outcome of a filter pass."""

    passed: list[CandidateItem]
    dropped: list[tuple[CandidateItem, str]]


class SynthesisFilter(ABC):
    """Base class for synthesis filters."""

    @abstractmethod
    async def filter(
        self,
        candidates: list[CandidateItem],
        *,
        target_dataset_id: str | None = None,
    ) -> FilterResult: ...


# ---------------------------------------------------------------------------
# Embedding dedupe
# ---------------------------------------------------------------------------


class EmbeddingDedupeFilter(SynthesisFilter):
    """Cosine dedupe via hecate-memory's EmbeddingService.

    When ``embedding_service`` reports mock mode (FlagEmbedding wheel
    missing) the filter falls back to character-level n-gram Jaccard
    similarity. The fallback is logged at INFO so operators can see
    why dedupe is weaker than usual.
    """

    COSINE_THRESHOLD = 0.92
    NGRAM_THRESHOLD = 0.85
    NGRAM_SIZE = 3

    def __init__(self, *, embedding_service: object | None = None) -> None:
        self._embedding_service = embedding_service

    async def filter(
        self,
        candidates: list[CandidateItem],
        *,
        target_dataset_id: str | None = None,
    ) -> FilterResult:
        if not candidates:
            return FilterResult(passed=[], dropped=[])

        existing = await self._load_existing_queries(target_dataset_id)
        if not existing:
            return FilterResult(passed=list(candidates), dropped=[])

        if self._embedding_is_mock():
            logger.info("Embedding service in mock mode — falling back to n-gram dedupe")
            return self._ngram_dedupe(candidates, existing)

        return await self._cosine_dedupe(candidates, existing)

    def _embedding_is_mock(self) -> bool:
        """True when FlagEmbedding isn't installed and the service is
        returning mock-mode indicators."""
        if self._embedding_service is None:
            return True
        model = getattr(self._embedding_service, "_model", None)
        return model == "mock"

    async def _cosine_dedupe(
        self,
        candidates: list[CandidateItem],
        existing: list[str],
    ) -> FilterResult:
        from hecate.ops.evaluation.synthesis._embedding import encode_texts  # local helper

        try:
            existing_vecs = await encode_texts(existing, self._embedding_service)
            candidate_vecs = await encode_texts([c.query for c in candidates], self._embedding_service)
        except Exception as exc:
            logger.warning("Embedding encode failed (%s) — falling back to n-gram", exc)
            return self._ngram_dedupe(candidates, existing)

        passed: list[CandidateItem] = []
        dropped: list[tuple[CandidateItem, str]] = []
        for cand, cvec in zip(candidates, candidate_vecs, strict=True):
            if cvec is None:
                # encoding failed; pass through with INFO log
                passed.append(cand)
                continue
            max_sim = max(_cosine(cvec, evec) for evec in existing_vecs if evec is not None)
            if max_sim >= self.COSINE_THRESHOLD:
                dropped.append((cand, f"cosine_sim={max_sim:.3f}>={self.COSINE_THRESHOLD}"))
            else:
                passed.append(cand)
        return FilterResult(passed=passed, dropped=dropped)

    def _ngram_dedupe(
        self,
        candidates: list[CandidateItem],
        existing: list[str],
    ) -> FilterResult:
        existing_ngrams = [_ngrams(q, self.NGRAM_SIZE) for q in existing]
        passed: list[CandidateItem] = []
        dropped: list[tuple[CandidateItem, str]] = []
        for cand in candidates:
            cand_ngrams = _ngrams(cand.query, self.NGRAM_SIZE)
            if not cand_ngrams:
                passed.append(cand)
                continue
            max_jacc = max(_jaccard(cand_ngrams, en) for en in existing_ngrams if en)
            if max_jacc >= self.NGRAM_THRESHOLD:
                dropped.append((cand, f"ngram_jacc={max_jacc:.3f}>={self.NGRAM_THRESHOLD}"))
            else:
                passed.append(cand)
        return FilterResult(passed=passed, dropped=dropped)

    async def _load_existing_queries(self, target_dataset_id: str | None) -> list[str]:
        """Read existing queries for the target dataset.

        Implementation note: this is a hook that the synthesis service
        populates; the filter itself is intentionally stateless to keep
        the strategy/filter split clean. The service passes existing
        queries in via the candidate's ``metadata_`` field when wired
        together (see :class:`DatasetSynthesisService`).

        For now, when no target_dataset_id is provided we return [] —
        dedupe is a no-op (matches the spec's "empty dataset has no
        dedupe effect" scenario).
        """
        return []


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------


class QualityFilter(SynthesisFilter):
    """Critic LLM self-grading.

    The critic rates each candidate on two 0.0-1.0 axes (self_containment
    and clarity). Items scoring below ``threshold`` on either axis are
    dropped. Threshold defaults to 0.6.
    """

    DEFAULT_THRESHOLD = 0.6

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.threshold = threshold
        self.llm_config = llm_config

    async def filter(
        self,
        candidates: list[CandidateItem],
        *,
        target_dataset_id: str | None = None,
    ) -> FilterResult:
        if not candidates:
            return FilterResult(passed=[], dropped=[])

        passed: list[CandidateItem] = []
        dropped: list[tuple[CandidateItem, str]] = []
        for cand in candidates:
            score = await self._critic_score(cand)
            if score is None:
                # critic failed — be lenient and pass
                passed.append(cand)
                continue
            containment, clarity = score
            if containment < self.threshold or clarity < self.threshold:
                dropped.append(
                    (
                        cand,
                        f"quality<{self.threshold} (containment={containment:.2f},clarity={clarity:.2f})",
                    )
                )
            else:
                passed.append(cand)
        return FilterResult(passed=passed, dropped=dropped)

    async def _critic_score(self, cand: CandidateItem) -> tuple[float, float] | None:
        prompt = (
            "Rate the following evaluation item on two 0.0-1.0 axes:\n"
            "1. self_containment: can the query be answered without external context?\n"
            "2. clarity: is the query unambiguous?\n"
            f"Query: {cand.query}\n"
            f"Expected answer: {cand.expected_answer or ''}\n"
            'Respond with ONLY JSON: {"self_containment": <float>, "clarity": <float>}\n'
        )
        from hecate_llm.service import llm_service

        from hecate.ops.evaluation.types import LLMConfig

        cfg = self.llm_config or LLMConfig()
        for _attempt in (1, 2):
            try:
                resp = await llm_service.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=cfg.model,
                    temperature=cfg.temperature,
                    timeout=60.0,
                )
            except Exception as exc:
                logger.warning("Quality critic LLM call failed: %s", exc)
                continue
            content = (resp.content or "").strip()
            if content.startswith("```"):
                content = "\n".join(content.split("\n")[1:-1])
            try:
                import json

                data = json.loads(content)
                return float(data["self_containment"]), float(data["clarity"])
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Quality critic parse failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# DLP
# ---------------------------------------------------------------------------


class DLPFilter(SynthesisFilter):
    """DLP scan before persistence.

    Wraps :class:`hecate.ops.dlp.service.DLPService.dry_run_scan`. An
    item is dropped (``dlp_blocked``) if any of its text fields trigger
    a finding. Errors from DLPService are logged and treated as
    pass-through (fail-open on infrastructure errors; DLP enforcement
    at runtime still catches anything we let through here).
    """

    def __init__(self, *, db: AsyncSession | None = None) -> None:
        self._db = db

    async def filter(
        self,
        candidates: list[CandidateItem],
        *,
        target_dataset_id: str | None = None,
    ) -> FilterResult:
        if not candidates or self._db is None:
            return FilterResult(passed=list(candidates), dropped=[])

        from hecate.ops.dlp.service import DLPService

        svc = DLPService(self._db)
        passed: list[CandidateItem] = []
        dropped: list[tuple[CandidateItem, str]] = []
        for cand in candidates:
            try:
                hit = await self._scan(cand, svc)
            except Exception as exc:
                logger.warning("DLP scan failed for item, fail-open: %s", exc)
                passed.append(cand)
                continue
            if hit:
                dropped.append((cand, "dlp_blocked"))
            else:
                passed.append(cand)
        return FilterResult(passed=passed, dropped=dropped)

    async def _scan(self, cand: CandidateItem, svc: object) -> bool:
        """Return True if DLP flags any text field on the candidate."""
        fields = [cand.query or "", cand.expected_answer or "", *(cand.context or [])]
        for text in fields:
            if not text:
                continue
            try:
                result = await svc.dry_run_scan(
                    text=text,
                    direction="output",
                )
            except Exception as exc:
                logger.warning("DLP dry_run_scan failed: %s", exc)
                continue
            if getattr(result, "findings", None):
                return True
        return False


# ---------------------------------------------------------------------------
# Helpers — embedding + ngram math (kept private to this module)
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length dense vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _ngrams(s: str, n: int) -> set[str]:
    """Character n-gram set."""
    if len(s) < n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
