"""Runtime grounding scoring for tool-grounded agent responses (1.3.5e Stage 2).

Scores the factual sentences of an LLM response against their evidence and
records the verdicts as an additive ``GROUNDING_SCORE`` audit event. The
layer is strictly observational: it never blocks, rewrites, or reorders
delivery, and a contradicted response is delivered exactly as generated.

Pipeline per scored response:

1. **Claim inventory** — the factual sentences shared with the citation
   ratio signal (D8 heuristic).
2. **Evidence acquisition** — cited chunks resolved through the session
   registry; uncited sentences optionally go through fallback knowledge
   retrieval when the policy configures it; everything else is scored
   ``unverifiable`` without a backend call.
3. **Scoring** — a pluggable :class:`GroundingScorer` backend selected by
   policy: an LLM judge via the platform model invocation path, or an HTTP
   NLI endpoint (self-hosted MiniCheck-class models). Escalation upgrades
   low-confidence pairs from the NLI backend to the judge.
4. **Aggregation** — response-level verdict counts/ratios plus a
   ``would_block`` shadow disposition computed against (default code
   constant) thresholds. The shadow disposition has no behavioral effect;
   it exists to calibrate the Stage 3 action surface against real traffic.

Red line (see specs): a ``contradicted`` verdict requires the backend to
report explicit evidence disagreement. Low confidence is never folded into
the verdict — weakly supported stays ``supported`` with low confidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from hecate.runtime.citation_provenance import MARKER_PATTERN, factual_sentences
from hecate.runtime.grounding_policy import EscalationConfig, GroundingPolicy

logger = logging.getLogger(__name__)

# Audit event name (EventType.CUSTOM payload, BUDGET_SNAPSHOT pattern).
EVENT_NAME_SCORE = "GROUNDING_SCORE"
# Execution-context key carrying the agent-level grounding policy dict
# (aligned with the ``citation_provenance`` / ``context_chain`` passthroughs).
EXECUTION_CONTEXT_KEY = "grounding_scoring"
# Chain stage identity for the scoring stage on the LLM_RESPONSE phase.
STAGE_ID = "grounding-scoring"

VERDICT_SUPPORTED = "supported"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_UNVERIFIABLE = "unverifiable"
_VALID_VERDICTS = (VERDICT_SUPPORTED, VERDICT_CONTRADICTED, VERDICT_UNVERIFIABLE)

EVIDENCE_CITED = "cited"
EVIDENCE_FALLBACK = "fallback"
EVIDENCE_NONE = "none"

# Confidence assigned by binary-support HTTP endpoints, which report no
# calibration of their own; the verdict mapping is documented in D3.
_BINARY_SUPPORT_CONFIDENCE = 0.8

# Judge output parsing: retry the batch once before degrading all pairs.
_JUDGE_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class EvidenceRef:
    """Provenance of one evidence item used for a claim."""

    kind: str  # cited | fallback | none
    ref: str | None  # chunk marker ("2-3") or fallback source label
    truncated: bool = False


@dataclass(frozen=True)
class ClaimEvidencePair:
    """One factual claim bundled with its acquired evidence texts."""

    claim: str
    evidences: list[str] = field(default_factory=list)
    refs: list[EvidenceRef] = field(default_factory=list)


@dataclass(frozen=True)
class ScoringResult:
    """Verdict for one claim, aligned by index with its pair."""

    claim: str
    verdict: str
    confidence: float
    evidence_kind: str
    evidence_ref: str | None = None
    evidence_truncated: bool = False
    degraded: bool = False
    detail: str = ""


class GroundingScorer(ABC):
    """Backend seam for claim-evidence grounding scoring.

    Implementations return exactly one :class:`ScoringResult` per input
    pair, aligned by index. Backend failures degrade individual results
    (``degraded=True``) instead of raising — scoring never fails an
    invocation.
    """

    @abstractmethod
    async def score(self, pairs: list[ClaimEvidencePair]) -> list[ScoringResult]:
        """Score each (claim, evidence) pair; results align by index."""


def _result_for(
    pair: ClaimEvidencePair, verdict: str, confidence: float, *, degraded: bool = False, detail: str = ""
) -> ScoringResult:
    primary_ref = pair.refs[0] if pair.refs else EvidenceRef(kind=EVIDENCE_NONE, ref=None)
    return ScoringResult(
        claim=pair.claim,
        verdict=verdict,
        confidence=confidence,
        evidence_kind=primary_ref.kind,
        evidence_ref=primary_ref.ref,
        evidence_truncated=primary_ref.truncated,
        degraded=degraded,
        detail=detail,
    )


def _normalized_confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not 0.0 <= numeric <= 1.0:
        return None
    return numeric


class LlmJudgeScorer(GroundingScorer):
    """Batch LLM judge backend over the platform model invocation path.

    All pairs are judged in one invocation (cost/latency: linear in tokens,
    constant in round-trips). The judge is prompted for an explicit
    three-way verdict per pair; malformed output retries once, then the
    affected pairs degrade.
    """

    def __init__(self, port: Any, model: str) -> None:
        self._port = port
        self._model = model

    def _prompt(self, pairs: list[ClaimEvidencePair]) -> str:
        lines = [
            "You are a strict fact-checking judge. For each numbered pair, decide whether",
            "the evidence supports the claim. Verdicts:",
            '- "supported": the evidence contains the information asserted by the claim',
            '- "contradicted": the evidence explicitly states the opposite',
            '- "unverifiable": the evidence is insufficient to decide',
            "Respond with ONLY a JSON array:",
            '[{"index": <pair number>, "verdict": "supported|contradicted|unverifiable", "confidence": <0.0-1.0>}]',
            "",
            "Pairs:",
        ]
        for i, pair in enumerate(pairs, start=1):
            evidence = "\n---\n".join(pair.evidences) if pair.evidences else "(no evidence)"
            lines.append(f"[{i}] claim: {pair.claim}\nevidence: {evidence}")
        return "\n".join(lines)

    def _parse(self, raw: str, pairs: list[ClaimEvidencePair]) -> list[ScoringResult]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("no JSON array in judge output")
        entries = json.loads(text[start : end + 1])
        by_index: dict[int, dict[str, Any]] = {}
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("index"), int):
                    by_index[entry["index"]] = entry
        results: list[ScoringResult] = []
        for i, pair in enumerate(pairs, start=1):
            entry = by_index.get(i)
            verdict = entry.get("verdict") if entry else None
            confidence = _normalized_confidence(entry.get("confidence")) if entry else None
            if verdict in _VALID_VERDICTS and confidence is not None:
                results.append(_result_for(pair, str(verdict), confidence))
            else:
                results.append(
                    _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="judge output unusable")
                )
        return results

    async def _invoke(self, prompt: str) -> str:
        chunks: list[str] = []
        async for token in self._port.llm_invoke(
            messages=[{"role": "user", "content": prompt}], config={"model": self._model}
        ):
            chunks.append(token)
        return "".join(chunks)

    async def score(self, pairs: list[ClaimEvidencePair]) -> list[ScoringResult]:
        if not pairs:
            return []
        prompt = self._prompt(pairs)
        raw = ""
        for attempt in range(1, _JUDGE_MAX_ATTEMPTS + 1):
            try:
                raw = await self._invoke(prompt)
                return self._parse(raw, pairs)
            except Exception as exc:  # noqa: BLE001 — degradation is the contract
                logger.warning("LLM judge scoring attempt %d failed: %s", attempt, exc)
        return [
            _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="judge unavailable") for pair in pairs
        ]


class HttpNliScorer(GroundingScorer):
    """HTTP NLI backend against an operator-configured endpoint.

    Enables self-hosted entailment models (MiniCheck-class) without
    embedding a model runtime in the platform. Accepts two response item
    shapes per pair: ``{"verdict": ..., "confidence": ...}`` (three-way) or
    ``{"support": bool}`` (binary — a ``false`` maps to ``unverifiable``
    and can never yield ``contradicted``).
    """

    def __init__(self, endpoint: str, timeout_ms_per_pair: int = 500) -> None:
        self._endpoint = endpoint
        self._timeout_ms_per_pair = timeout_ms_per_pair

    @staticmethod
    def _parse_items(payload: Any, pairs: list[ClaimEvidencePair]) -> list[ScoringResult]:
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("missing results array in NLI response")
        items = payload["results"]
        results: list[ScoringResult] = []
        for i, pair in enumerate(pairs):
            item = items[i] if i < len(items) and isinstance(items[i], dict) else None
            results.append(HttpNliScorer._parse_item(pair, item))
        return results

    @staticmethod
    def _parse_item(pair: ClaimEvidencePair, item: dict[str, Any] | None) -> ScoringResult:
        if item is None:
            return _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="nli result missing")
        if "verdict" in item:
            verdict = item.get("verdict")
            confidence = _normalized_confidence(item.get("confidence"))
            if verdict in _VALID_VERDICTS and confidence is not None:
                return _result_for(pair, str(verdict), confidence)
            return _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="nli verdict unusable")
        if "support" in item and isinstance(item.get("support"), bool):
            if item["support"]:
                confidence = _normalized_confidence(item.get("confidence")) or _BINARY_SUPPORT_CONFIDENCE
                return _result_for(pair, VERDICT_SUPPORTED, confidence)
            return _result_for(pair, VERDICT_UNVERIFIABLE, 0.0)
        return _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="nli item shape unknown")

    async def score(self, pairs: list[ClaimEvidencePair]) -> list[ScoringResult]:
        if not pairs:
            return []
        timeout_s = min(15.0, max(1.0, self._timeout_ms_per_pair * len(pairs) / 1000))
        body = {
            "pairs": [
                {"claim": pair.claim, "evidence": "\n---\n".join(pair.evidences) if pair.evidences else ""}
                for pair in pairs
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(self._endpoint, json=body)
                response.raise_for_status()
                payload: Any = response.json()
        except Exception as exc:  # noqa: BLE001 — degradation is the contract
            logger.warning("HTTP NLI scoring failed on '%s': %s", self._endpoint, exc)
            return [
                _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="nli endpoint unavailable")
                for pair in pairs
            ]
        try:
            return self._parse_items(payload, pairs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HTTP NLI response mapping failed: %s", exc)
            return [
                _result_for(pair, VERDICT_UNVERIFIABLE, 0.0, degraded=True, detail="nli response unusable")
                for pair in pairs
            ]


class EscalatingScorer(GroundingScorer):
    """Primary backend with judge escalation for low-confidence pairs.

    Wraps an NLI-class primary scorer: results whose confidence falls below
    ``config.confidence`` are re-scored once by the judge backend (capped at
    ``config.max_pairs``); judge results replace the primary ones unless the
    escalation itself degrades.
    """

    def __init__(self, primary: GroundingScorer, judge: LlmJudgeScorer, config: EscalationConfig) -> None:
        self._primary = primary
        self._judge = judge
        self._config = config

    async def score(self, pairs: list[ClaimEvidencePair]) -> list[ScoringResult]:
        results = list(await self._primary.score(pairs))
        if not self._config.enabled:
            return results
        candidates = [i for i, r in enumerate(results) if not r.degraded and r.confidence < self._config.confidence]
        candidates = candidates[: self._config.max_pairs]
        if not candidates:
            return results
        try:
            escalated = await self._judge.score([pairs[i] for i in candidates])
        except Exception as exc:  # noqa: BLE001 — keep primary results on failure
            logger.warning("Escalation scoring failed: %s", exc)
            return results
        for i, escalation_result in zip(candidates, escalated, strict=True):
            if not escalation_result.degraded:
                results[i] = escalation_result
        return results


def build_scorer(policy: GroundingPolicy, port: Any, default_model: str) -> GroundingScorer:
    """Construct the policy-selected scorer (with escalation when configured)."""
    if policy.backend == "http_nli":
        primary: GroundingScorer = HttpNliScorer(policy.nli_endpoint or "", policy.nli_timeout_ms)
        judge_model = policy.model or default_model
        if policy.escalation.enabled:
            return EscalatingScorer(primary, LlmJudgeScorer(port, judge_model), policy.escalation)
        return primary
    return LlmJudgeScorer(port, policy.model or default_model)


def build_claim_pairs(
    response_text: str,
    registry: Any,
) -> tuple[list[ClaimEvidencePair], list[str]]:
    """Split the response into factual claims and acquire cited evidence.

    Returns ``(pairs, uncited_sentences)``. Sentences whose markers resolve
    in the session registry get their chunk texts as evidence; sentences
    with no resolvable marker (including invented markers) are returned in
    ``uncited_sentences`` for optional fallback retrieval.
    """
    pairs: list[ClaimEvidencePair] = []
    uncited: list[str] = []
    for sentence in factual_sentences(response_text):
        evidences: list[str] = []
        refs: list[EvidenceRef] = []
        for match in MARKER_PATTERN.finditer(sentence):
            entry = registry.resolve(f"{match.group(1)}-{match.group(2)}") if registry is not None else None
            if entry is not None:
                evidences.append(entry.text)
                refs.append(EvidenceRef(kind=EVIDENCE_CITED, ref=entry.marker, truncated=entry.truncated))
        if evidences:
            pairs.append(ClaimEvidencePair(claim=sentence, evidences=evidences, refs=refs))
        else:
            uncited.append(sentence)
    return pairs, uncited


async def acquire_fallback_evidence(
    port: Any,
    sentences: list[str],
    kb_ids: list[str],
    top_k: int,
) -> dict[str, list[str]]:
    """Retrieve fallback evidence for uncited sentences (per-sentence query).

    Best-effort: retrieval failures degrade to no evidence for that
    sentence rather than failing scoring. Returns a mapping for the
    sentences that produced at least one chunk.
    """
    if not sentences or not kb_ids:
        return {}
    from uuid import UUID

    parsed_ids: list[UUID] = []
    for kb_id in kb_ids:
        try:
            parsed_ids.append(UUID(kb_id) if isinstance(kb_id, str) else kb_id)
        except (ValueError, AttributeError):
            logger.warning("Skipping invalid fallback kb_id %r", kb_id)
    if not parsed_ids:
        return {}

    async def one(sentence: str) -> tuple[str, list[str]]:
        try:
            chunks = await port.knowledge_query(query=sentence, kb_ids=parsed_ids)
            texts = [str(c.get("content", "")) for c in (chunks or [])[:top_k] if isinstance(c, dict)]
            return sentence, [t for t in texts if t]
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            logger.warning("Fallback retrieval failed for a claim: %s", exc)
            return sentence, []

    pairs = await asyncio.gather(*(one(s) for s in sentences))
    return {sentence: texts for sentence, texts in pairs if texts}


_SYSTEM_RANDOM = random.SystemRandom()


def score_triggered(policy: GroundingPolicy, response_text: str) -> bool:
    """Evaluate the policy trigger against the response (D7)."""
    if policy.trigger == "always":
        return bool(factual_sentences(response_text))
    if policy.trigger == "sample":
        return _SYSTEM_RANDOM.random() < policy.sample_rate
    # on_uncited: score only when at least one factual sentence lacks a marker.
    ratio, _factual, uncited = _uncited_ratio(response_text)
    return uncited > 0 or ratio > 0.0


def _uncited_ratio(response_text: str) -> tuple[float, int, int]:
    from hecate.runtime.citation_provenance import uncited_ratio

    return uncited_ratio(response_text)


async def score_response(
    policy: GroundingPolicy,
    registry: Any,
    port: Any,
    response_text: str,
    default_model: str,
) -> dict[str, Any] | None:
    """Score one response end-to-end; returns the event payload or None.

    None means "nothing scored" (no factual sentences, or no claims after
    evidence acquisition with fallback disabled). Failures inside the
    scorer degrade individual results; this function itself does not raise
    under normal operation — the caller treats it as best-effort.
    """
    sentences = factual_sentences(response_text)
    if not sentences:
        return None
    pairs, uncited = build_claim_pairs(response_text, registry)
    if uncited and policy.fallback.enabled and policy.fallback.kb_ids:
        fallback = await acquire_fallback_evidence(port, uncited, policy.fallback.kb_ids, policy.fallback.top_k)
        for sentence, texts in fallback.items():
            pairs.append(
                ClaimEvidencePair(
                    claim=sentence,
                    evidences=texts,
                    refs=[EvidenceRef(kind=EVIDENCE_FALLBACK, ref="fallback_retrieval")],
                )
            )
    scorer_pairs = pairs if pairs else [ClaimEvidencePair(claim=s) for s in uncited]
    if policy.fallback.enabled or pairs:
        results = await build_scorer(policy, port, default_model).score(scorer_pairs)
    else:
        # Fallback disabled: uncited claims are unverifiable without calls.
        results = [_result_for(pair, VERDICT_UNVERIFIABLE, 0.0) for pair in scorer_pairs]
    return build_scoring_payload(results)


def aggregate_results(results: list[ScoringResult]) -> dict[str, Any]:
    """Response-level verdict counts and ratios over all scored claims."""
    total = len(results)
    counts = {v: sum(1 for r in results if r.verdict == v) for v in _VALID_VERDICTS}
    degraded = sum(1 for r in results if r.degraded)

    def ratio(count: int) -> float:
        return round(count / total, 4) if total else 0.0

    return {
        "claims_total": total,
        "supported": counts[VERDICT_SUPPORTED],
        "contradicted": counts[VERDICT_CONTRADICTED],
        "unverifiable": counts[VERDICT_UNVERIFIABLE],
        "degraded": degraded,
        "supported_ratio": ratio(counts[VERDICT_SUPPORTED]),
        "contradicted_ratio": ratio(counts[VERDICT_CONTRADICTED]),
        "unverifiable_ratio": ratio(counts[VERDICT_UNVERIFIABLE]),
    }


def would_block_disposition(results: list[ScoringResult], thresholds: Any) -> dict[str, Any]:
    """Compute the shadow disposition (no behavioral effect — Stage 3 calibration)."""
    aggregates = aggregate_results(results)
    reasons: list[str] = []
    if thresholds.contradicted_any and aggregates["contradicted"] > 0:
        reasons.append("any_claim_contradicted")
    if aggregates["contradicted_ratio"] > thresholds.contradicted_ratio:
        reasons.append("contradicted_ratio")
    if aggregates["unverifiable_ratio"] > thresholds.unsupported_ratio:
        reasons.append("unverifiable_ratio")
    return {"triggered": bool(reasons), "reason": ",".join(reasons)}


def build_scoring_payload(results: list[ScoringResult], thresholds: Any | None = None) -> dict[str, Any]:
    """Assemble the ``GROUNDING_SCORE`` event payload (design D8)."""
    from hecate.runtime.grounding_policy import ShadowThresholds

    effective = thresholds or ShadowThresholds()
    per_claim = [
        {
            "claim": r.claim,
            "verdict": r.verdict,
            "confidence": round(r.confidence, 4),
            "evidence": {"kind": r.evidence_kind, "ref": r.evidence_ref, "truncated": r.evidence_truncated},
            "degraded": r.degraded,
        }
        for r in results
    ]
    return {
        "per_claim": per_claim,
        "aggregates": aggregate_results(results),
        "would_block": would_block_disposition(results, effective),
    }
