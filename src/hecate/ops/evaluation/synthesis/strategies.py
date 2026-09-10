"""Synthesis strategies for dataset generation.

Three strategies, each producing a stream of :class:`CandidateItem`:

- :class:`GenerationStrategy` — direct Q&A generation from topic or seed
- :class:`EvolutionStrategy` — applies one of three evolution types
  (REASONING / HYPOTHETICAL / IN_BREADTH) sampled uniformly per item
- :class:`AdversarialStrategy` — combines an intent with a base
  transformation. v1 ships 5 intents × 1 base transformation each.

All strategies share:

- One LLM call per candidate
- Configurable concurrency cap (default 5), via :func:`_gather_concurrent`
- Two-attempt retry on LLM call failure, then marked ``generation_failed``
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from hecate.ops.evaluation.synthesis import CandidateItem
from hecate.ops.evaluation.synthesis.prompts import (
    ADVERSARIAL_PROMPTS,
    EVOLUTION_PROMPTS,
    GENERATION_PROMPT,
)
from hecate.ops.evaluation.types import LLMConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


async def _call_llm_json(prompt: str, llm_config: LLMConfig | None) -> dict | None:
    """One-shot LLM call expecting JSON; returns parsed dict or None."""
    from hecate_llm.service import llm_service

    config = llm_config or LLMConfig()
    for attempt in (1, 2):
        try:
            response = await llm_service.chat(
                messages=[{"role": "user", "content": prompt}],
                model=config.model,
                temperature=config.temperature,
                timeout=60.0,
            )
        except Exception as exc:
            logger.warning("Synthesis LLM call attempt %d failed: %s", attempt, exc)
            continue
        content = (response.content or "").strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Synthesis JSON parse attempt %d failed: %s", attempt, exc)
    return None


def _format_seed(seed: Any) -> str:
    """Render a seed (None / topic string / dict / CandidateItem) as a
    human-readable snippet for prompts."""
    if seed is None:
        return "(none — generate freely)"
    if isinstance(seed, str):
        return seed
    if isinstance(seed, dict):
        return json.dumps(seed, ensure_ascii=False)
    if isinstance(seed, CandidateItem):
        return json.dumps(
            {"query": seed.query, "expected_answer": seed.expected_answer},
            ensure_ascii=False,
        )
    return str(seed)


class SynthesisStrategy(ABC):
    """Base class for synthesis strategies."""

    def __init__(
        self,
        *,
        concurrency: int = 5,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.concurrency = concurrency
        self.llm_config = llm_config

    @abstractmethod
    async def generate(
        self,
        *,
        seeds: list[Any],
        count: int,
        topic: str | None = None,
        intent: str | None = None,
    ) -> list[CandidateItem]:
        """Produce candidate items. Length <= count. May be shorter on
        generation failures (caller counts them via missing items)."""


class GenerationStrategy(SynthesisStrategy):
    """Direct Q&A generation from topic or seed.

    When ``seeds`` is non-empty, each seed becomes one paraphrase.
    When ``topic`` is non-empty, the topic seeds up to ``count`` items.
    Both can be combined.
    """

    async def generate(
        self,
        *,
        seeds: list[Any],
        count: int,
        topic: str | None = None,
        intent: str | None = None,
    ) -> list[CandidateItem]:
        # Build the work queue: one item per seed, then top up with topic
        work: list[Any] = list(seeds)[:count]
        if len(work) < count and topic:
            # pad with N copies of the topic to drive free generation
            pad = min(count - len(work), count)
            work.extend([topic] * pad)

        async def one(seed: Any) -> CandidateItem | None:
            prompt = GENERATION_PROMPT.format(seed=_format_seed(seed))
            data = await _call_llm_json(prompt, self.llm_config)
            if not data or "query" not in data:
                return None
            return CandidateItem(
                query=str(data["query"]).strip(),
                expected_answer=str(data.get("expected_answer", "")).strip() or None,
                strategy="generation",
            )

        results = await _gather_concurrent([one(s) for s in work], self.concurrency)
        return [r for r in results if r is not None]


class EvolutionStrategy(SynthesisStrategy):
    """Three evolution types (REASONING / HYPOTHETICAL / IN_BREADTH).

    Each seed is evolved once, with the evolution type sampled uniformly
    across the three options. When ``seeds`` is empty, the strategy falls
    back to topic-grounded evolution by treating ``topic`` as a single seed.
    """

    TYPES: tuple[str, ...] = ("REASONING", "HYPOTHETICAL", "IN_BREADTH")

    async def generate(
        self,
        *,
        seeds: list[Any],
        count: int,
        topic: str | None = None,
        intent: str | None = None,
    ) -> list[CandidateItem]:
        work: list[Any] = list(seeds)[:count]
        if len(work) < count and topic:
            work.extend([topic] * (count - len(work)))
        rng = random.SystemRandom()  # noqa: S311 — non-cryptographic use is fine here

        async def one(seed: Any) -> CandidateItem | None:
            evo_type = rng.choice(self.TYPES)
            prompt = EVOLUTION_PROMPTS[evo_type].format(seed=_format_seed(seed))
            data = await _call_llm_json(prompt, self.llm_config)
            if not data or "query" not in data:
                return None
            return CandidateItem(
                query=str(data["query"]).strip(),
                expected_answer=str(data.get("expected_answer", "")).strip() or None,
                strategy="evolution",
                seed_item_id=str(getattr(seed, "id", None) or uuid.uuid4()),
                extra_tags=[f"evolution_type:{evo_type.lower()}"],
            )

        results = await _gather_concurrent([one(s) for s in work], self.concurrency)
        return [r for r in results if r is not None]


class AdversarialStrategy(SynthesisStrategy):
    """Five intents × 1 base transformation (templated prompt).

    v1 does not implement Promptfoo-style N×M cartesian product — each
    intent maps to a single hand-written prompt template. The OWASP LLM
    Top-10 (2025 edition) is the taxonomy anchor, mirroring the existing
    ``injection-detection`` recognizers in
    ``runtime/security/hooks/injection_detection``.
    """

    VALID_INTENTS: tuple[str, ...] = (
        "prompt_injection_basic",
        "prompt_injection_indirect",
        "jailbreak_dan_style",
        "pii_extraction",
        "jailbreak_roleplay",
    )

    async def generate(
        self,
        *,
        seeds: list[Any],
        count: int,
        topic: str | None = None,
        intent: str | None = None,
    ) -> list[CandidateItem]:
        if intent is None or intent not in self.VALID_INTENTS:
            raise ValueError(f"Adversarial strategy requires intent in {self.VALID_INTENTS}, got {intent!r}")
        prompt_template = ADVERSARIAL_PROMPTS[intent]

        async def one(_idx: int) -> CandidateItem | None:
            prompt = prompt_template.format()  # intents don't use seed
            data = await _call_llm_json(prompt, self.llm_config)
            if not data or "query" not in data:
                return None
            return CandidateItem(
                query=str(data["query"]).strip(),
                expected_answer=str(data.get("expected_answer", "")).strip() or None,
                strategy="adversarial",
                extra_tags=[f"intent:{intent}"],
            )

        results = await _gather_concurrent([one(i) for i in range(count)], self.concurrency)
        return [r for r in results if r is not None]


async def _gather_concurrent(coros: list, concurrency: int) -> list:
    """Run coroutines with bounded concurrency. Order preserved."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def run(coro: Any) -> Any:
        async with sem:
            return await coro

    return await asyncio.gather(*[run(c) for c in coros])
