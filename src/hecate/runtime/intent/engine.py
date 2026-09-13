"""Default intent recognition engine (6.23 ⊕ 1.3.10).

Implements the layered pipeline over an ordered fast-path stack:

1. **Decision cache** (L1 hit → no other path runs).
2. **Patterns** — evidence-category regex/keyword fast path.
3. **Few-shot → LLM** — bounded, rotating sample evidence per category;
   the LLM returns JSON constrained to the evidence labels.
4. **Fallback** — no label (never a guess); degradation when the evidence
   port fails classifies over the config-known fallback labels instead.

The L2 (workflow) and L3 (session) layers derive from the atomic result
plus the carried session state — an explicit goal shift (cue phrase or the
same-call LLM verdict) replaces the session goal, a persistent mismatch
drifts it, otherwise routing stays sticky. The L4 domain label rides on
the matched category; the L5 policy flag is surfaced, never evaluated.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from hecate.runtime.intent.cache import (
    CachedDecision,
    IntentDecisionCache,
    fingerprint,
    normalize_utterance,
)
from hecate.runtime.intent.types import (
    AtomicIntent,
    DecisionSource,
    EvidencePayload,
    IntentEvidencePort,
    IntentRecognitionResult,
    IntentRecognizer,
    IntentRequest,
    WorkflowIntent,
)

logger = logging.getLogger(__name__)

# Utterance cues that signal an explicit session-goal shift (L3).
_SHIFT_CUE = re.compile(
    r"(换个话题|改成|现在要|另外|instead|switch to|actually,? (i|let)'s|new task)",
    re.IGNORECASE,
)
# Utterance cues that signal a multi-step task (L2), even on a single turn.
_MULTI_STEP_CUE = re.compile(
    r"(然后|接着|之后再|and then|after that|step \d|first.*then)",
    re.IGNORECASE,
)

_CONFIDENCE_PATTERN = 0.9
_CONFIDENCE_FALLBACK = 0.0


@dataclass
class IntentConfig:
    """Recognition engine tuning knobs.

    Attributes:
        few_shot_per_category: Maximum sample utterances per category in
            the few-shot prompt.
        few_shot_total_cap: Maximum total samples across categories.
        workflow_activation_turns: Consecutive related turns that activate
            the L2 workflow intent.
        shift_streak_threshold: Consecutive mismatched turns that drift the
            L3 session goal without an explicit shift cue.
        cache_max_size / cache_ttl_seconds: Decision cache bounds.
    """

    few_shot_per_category: int = 5
    few_shot_total_cap: int = 30
    workflow_activation_turns: int = 2
    shift_streak_threshold: int = 2
    cache_max_size: int = 2048
    cache_ttl_seconds: float = 600.0


async def _collect_llm(port: Any, messages: list[dict], config: dict) -> str:
    """Collect a streaming llm_invoke into one string (task_allocator pattern)."""
    chunks: list[str] = []
    async for token in port.llm_invoke(messages=messages, config=config):
        chunks.append(token)
    return "".join(chunks)


def _parse_llm_verdict(raw: str, labels: tuple[str, ...]) -> tuple[str | None, float, bool]:
    """Parse the classification JSON; invalid output degrades to fallback."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None, _CONFIDENCE_FALLBACK, False
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, _CONFIDENCE_FALLBACK, False
    label = verdict.get("label")
    if label not in labels:
        return None, _CONFIDENCE_FALLBACK, False
    try:
        confidence = max(0.0, min(1.0, float(verdict.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    goal_shift = bool(verdict.get("goal_shift", False))
    return str(label), confidence, goal_shift


class IntentRecognitionEngine(IntentRecognizer):
    """Default layered recognition engine.

    Args:
        port: RuntimePort-like object used only for LLM classification
            (``llm_invoke(messages=..., config=...)`` streaming). May be
            ``None`` in tests — the engine then stops at the pattern path.
        cache: Decision cache; a process-local default is created when omitted.
        config: Tuning knobs; defaults when omitted.
    """

    def __init__(
        self,
        port: Any = None,
        cache: IntentDecisionCache | None = None,
        config: IntentConfig | None = None,
    ) -> None:
        self._port = port
        self._cache = cache or IntentDecisionCache(
            max_size=(config or IntentConfig()).cache_max_size,
            ttl_seconds=(config or IntentConfig()).cache_ttl_seconds,
        )
        self._config = config or IntentConfig()

    async def recognize(self, request: IntentRequest) -> IntentRecognitionResult:
        """Run the layered pipeline for one user turn."""
        started = time.monotonic()
        utterance = request.utterance
        session = request.session_intent or {}
        prior_goal = session.get("goal")
        prior_workflow = session.get("workflow_label")
        turn_labels: list[str] = list(session.get("turn_labels", []))

        evidence = request.evidence
        categories = evidence.categories if evidence else ()
        context_fp = fingerprint(prior_goal, tuple(turn_labels[-3:]), tuple(c.name for c in categories))

        label: str | None = None
        confidence = _CONFIDENCE_FALLBACK
        source = DecisionSource.FALLBACK
        goal_shift = False
        cache_hit = False
        gated = False

        # L1 fast path 1: decision cache.
        if evidence is not None:
            key = IntentDecisionCache.make_key(utterance, evidence.version_id, context_fp)
            cached = self._cache.get(key)
            if cached is not None:
                label, confidence, cache_hit = cached.label, cached.confidence, True
                source = DecisionSource.CACHE
                gated = cached.gated

        # L1 fast path 2: category patterns.
        if label is None and categories:
            for category in categories:
                if any(re.search(pattern, utterance, re.IGNORECASE) for pattern in category.patterns):
                    label, confidence = category.name, _CONFIDENCE_PATTERN
                    source = DecisionSource.PATTERN
                    gated = category.policy_gated
                    break

        # L1 slow path: few-shot (evidence) or degraded (fallback labels) LLM.
        llm_shift = False
        if label is None:
            label_space = tuple(c.name for c in categories) or tuple(request.fallback_labels)
            if self._port is not None and label_space:
                raw = await self._classify_llm(request, categories, label_space, prior_goal)
                llm_label, llm_confidence, llm_shift = _parse_llm_verdict(raw, label_space)
                if llm_label is not None:
                    label, confidence = llm_label, llm_confidence
                    source = DecisionSource.FEW_SHOT if categories else DecisionSource.LLM
                    category = evidence.category(label) if evidence else None
                    gated = bool(category and category.policy_gated)

        category = evidence.category(label) if (evidence and label) else None
        if source is not DecisionSource.CACHE:
            gated = bool(category and category.policy_gated)
        domain = category.domain if category else None

        # L3: session-goal policy (explicit shift / drift / sticky).
        goal, goal_shift = self._update_goal(
            prior_goal=prior_goal,
            label=label,
            utterance=utterance,
            llm_shift=llm_shift,
            turn_labels=turn_labels,
        )
        if label is not None:
            turn_labels = (turn_labels + [label])[-5:]

        # L2: workflow intent over the accumulated turn window.
        workflow = await self._detect_workflow(
            turn_labels=turn_labels,
            current_label=label,
            prior_workflow=prior_workflow,
            utterance=utterance,
            goal=goal,
            port=self._port,
        )

        # Cache only successful non-cache decisions (a failed classification
        # must not pin the failure for the TTL).
        if (
            evidence is not None
            and not cache_hit
            and label is not None
            and source
            in (
                DecisionSource.FEW_SHOT,
                DecisionSource.LLM,
                DecisionSource.PATTERN,
            )
        ):
            self._cache.put(
                IntentDecisionCache.make_key(utterance, evidence.version_id, context_fp),
                CachedDecision(
                    label=label,
                    confidence=confidence,
                    gated=gated,
                    underlying_source=source.value,
                ),
            )

        latency_ms = round((time.monotonic() - started) * 1000, 2)
        return IntentRecognitionResult(
            atomic=AtomicIntent(label=label, confidence=confidence, source=source, gated=gated),
            workflow=workflow,
            domain=domain,
            gated=gated,
            evidence_ref=fingerprint(str(evidence.package_id), str(evidence.version_id)) if evidence else None,
            evidence_available=evidence is not None,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
            session_intent_update={
                "goal": goal,
                "turn_labels": turn_labels,
                "workflow_label": workflow.label if workflow.active else None,
            },
            utterance=utterance,
            goal_shift=goal_shift,
        )

    # ------------------------------------------------------------------
    # L1: LLM classification (few-shot or degraded)
    # ------------------------------------------------------------------

    async def _classify_llm(
        self,
        request: IntentRequest,
        categories: tuple,
        label_space: tuple[str, ...],
        prior_goal: str | None,
    ) -> str:
        """One LLM call producing the JSON verdict string."""
        if categories:
            # Bounded, rotating few-shot assembly: take the first N samples
            # per category, advancing a deterministic rotor each call so the
            # prompt varies across identical utterances (anti pattern-lock).
            rotor = fingerprint(normalize_utterance(request.utterance))
            rotor_int = int(rotor[:8], 16)
            catalog = []
            for index, category in enumerate(categories):
                samples = [sample for sample in category.samples]
                if samples:
                    offset = (rotor_int + index) % len(samples)
                    take = min(self._config.few_shot_per_category, len(samples))
                    picked = [samples[(offset + i) % len(samples)] for i in range(take)]
                else:
                    picked = []
                catalog.append(
                    {
                        "name": category.name,
                        "description": category.description,
                        "examples": picked[: self._config.few_shot_total_cap],
                    }
                )
            system = (
                "You classify the user's intent into exactly one category.\n"
                f"Categories: {json.dumps(catalog, ensure_ascii=False)}\n"
                "Respond with JSON only: "
                '{"label": "<category name or null>", "confidence": <0..1>, '
                '"goal_shift": <bool — true only when the user explicitly starts a new goal '
                "different from the session goal>}\n"
                "Never invent a label outside the category names."
            )
        else:
            system = (
                "You classify the user's intent into exactly one of the following labels: "
                f"{list(label_space)}\n"
                "Respond with JSON only: "
                '{"label": "<label or null>", "confidence": <0..1>, "goal_shift": <bool>}\n'
                "Never invent a label outside the list."
            )
        if prior_goal:
            system += f"\nCurrent session goal: {prior_goal}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": request.utterance},
        ]
        config = {"model": request.recognition_model} if request.recognition_model else {}
        return await _collect_llm(self._port, messages, config)

    # ------------------------------------------------------------------
    # L3: session-goal policy
    # ------------------------------------------------------------------

    def _update_goal(
        self,
        prior_goal: str | None,
        label: str | None,
        utterance: str,
        llm_shift: bool,
        turn_labels: list[str],
    ) -> tuple[str | None, bool]:
        """Merge policy: explicit shift replaces, drift replaces, else sticky."""
        if label is None:
            return prior_goal, False
        if prior_goal is None:
            return label, False
        if label == prior_goal:
            return prior_goal, False
        explicit = bool(_SHIFT_CUE.search(utterance)) or llm_shift
        if explicit:
            return label, True
        # Drift: the last N turns (current included) consistently disagree
        # with the stored goal.
        streak = 0
        for turn in reversed(turn_labels + [label]):
            if turn != prior_goal:
                streak += 1
            else:
                break
        if streak >= self._config.shift_streak_threshold:
            return label, True
        return prior_goal, False

    # ------------------------------------------------------------------
    # L2: workflow intent
    # ------------------------------------------------------------------

    async def _detect_workflow(
        self,
        turn_labels: list[str],
        current_label: str | None,
        prior_workflow: str | None,
        utterance: str,
        goal: str | None,
        port: Any,
    ) -> WorkflowIntent:
        """Activate on a consistent related-turn window or a multi-step cue."""
        window = [label for label in turn_labels if label]
        if current_label and current_label not in window[-1:]:
            window.append(current_label)
        window = window[-(self._config.workflow_activation_turns + 1) :]

        counts: dict[str, int] = {}
        for label in window:
            counts[label] = counts.get(label, 0) + 1
        dominant = max(counts, key=counts.get) if counts else None

        # A consistent window activates the workflow intent.
        if dominant and counts[dominant] >= self._config.workflow_activation_turns:
            return WorkflowIntent(label=dominant, active=True)

        # A single multi-step utterance activates on the goal/label.
        if _MULTI_STEP_CUE.search(utterance) and (current_label or goal):
            return WorkflowIntent(label=current_label or goal, active=True)

        # Ambiguous window while a workflow is active: one LLM continuation
        # verdict keeps the workflow from thrashing (best-effort).
        ambiguous = len(counts) > 1 and prior_workflow and port is not None and current_label
        if ambiguous and await self._llm_continuation(utterance, prior_workflow, port):
            return WorkflowIntent(label=prior_workflow, active=True)

        return WorkflowIntent(label=None, active=False)

    async def _llm_continuation(self, utterance: str, workflow_label: str, port: Any) -> bool:
        """Ask the LLM whether the turn continues the active workflow."""
        try:
            raw = await _collect_llm(
                port,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f'The session is working on the task "{workflow_label}". '
                            "Does the user message continue this task? "
                            'Respond JSON only: {"continue": <bool>}'
                        ),
                    },
                    {"role": "user", "content": utterance},
                ],
                config={},
            )
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            return bool(match) and bool(json.loads(match.group(0)).get("continue", False))
        except Exception:
            logger.exception("Workflow continuation check failed; treating as not continued")
            return False


__all__ = [
    "IntentConfig",
    "IntentRecognitionEngine",
    "resolve_evidence",
]


async def resolve_evidence(
    evidence_port: IntentEvidencePort,
    package_id: uuid.UUID,
    version_id: uuid.UUID | None = None,
) -> EvidencePayload | None:
    """Resolve evidence through the port, degrading to None on failure.

    Callers pass ``None`` evidence to the engine, which then classifies
    over the config-known fallback labels and marks the result as lacking
    evidence — a studio-side outage never fails the user turn.
    """
    try:
        return await evidence_port.get_evidence(package_id, version_id)
    except Exception:
        logger.exception("Evidence resolution failed for package %s", package_id)
        return None
