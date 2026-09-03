"""Typed data structures for the evaluation framework.

Defines the core data types shared across all evaluators:

- **Score** — a single metric measurement with name, value, reasoning, and source
- **EvalInput** — the input payload for an evaluator (query, contexts, answer, etc.)
- **EvalOutput** — the result of an evaluation (list of scores + metadata)
- **LLMConfig** — per-evaluator LLM configuration (model, temperature, api_base)
- **EvaluationRunResult** — aggregated results from a full evaluation run
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

VALID_SOURCES = ("llm_judge", "deterministic", "human")


class AnswerSource(StrEnum):
    """Determines how the generated answer is obtained for evaluation.

    - **MANUAL** — answers are pre-populated in ``EvaluationItem.generated_answer``
    - **PIPELINE** — run the RAG pipeline to generate answers at evaluation time
    - **AUTO** — use pre-populated answers when available, otherwise run the pipeline
    """

    MANUAL = "manual"
    PIPELINE = "pipeline"
    AUTO = "auto"


@dataclass
class Score:
    """A single evaluation metric result.

    Attributes:
        metric_name: Name of the metric (e.g. "faithfulness", "correctness").
        value: Metric value in the 0.0–1.0 range. Use -1.0 to signal
            that the metric could not be computed.
        reasoning: Optional explanation of the score.
        source: How the score was produced — ``"llm_judge"``,
            ``"deterministic"``, or ``"human"``.

    Raises:
        ValueError: If ``value`` is outside [-1.0, 1.0] or ``source``
            is not one of the valid sources.
    """

    metric_name: str
    value: float
    reasoning: str | None = None
    source: Literal["llm_judge", "deterministic", "human"] = "llm_judge"

    def __post_init__(self) -> None:
        if not -1.0 <= self.value <= 1.0:
            msg = f"Score value must be in [-1.0, 1.0], got {self.value}"
            raise ValueError(msg)
        valid = ("llm_judge", "deterministic", "human")
        if self.source not in valid:
            msg = f"Score source must be one of {valid}, got {self.source!r}"
            raise ValueError(msg)


@dataclass
class LLMConfig:
    """Per-evaluator LLM configuration.

    Each evaluator can use a different model, temperature, and API base.

    Attributes:
        model: LLM model identifier (e.g. ``"gpt-4o"``).
        temperature: Sampling temperature. Defaults to 0.0 for deterministic
            evaluation output.
        api_base: Optional custom API base URL.
    """

    model: str = "gpt-4o"
    temperature: float = 0.0
    api_base: str | None = None


@dataclass
class EvalInput:
    """Input payload for an evaluator.

    Attributes:
        query: The user's original query / question.
        retrieved_contexts: Context chunks retrieved by RAG (empty for agent
            evaluation).
        generated_answer: The answer produced by the system.
        expected_answer: Ground-truth answer for comparison (optional).
        tool_calls: Tool invocations made by the agent (optional).
        metadata: Arbitrary metadata attached to this evaluation item.
    """

    query: str
    retrieved_contexts: list[str] = field(default_factory=list)
    generated_answer: str = ""
    expected_answer: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalOutput:
    """Result of evaluating a single item.

    Attributes:
        scores: List of metric scores produced by the evaluator.
        metadata: Arbitrary metadata about the evaluation (e.g. token usage).
        duration_ms: Wall-clock time spent evaluating, in milliseconds.
    """

    scores: list[Score] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class EvaluationRunResult:
    """Aggregated results from a full evaluation run.

    Attributes:
        run_id: Unique identifier for this run.
        dataset_id: The dataset that was evaluated.
        item_scores: Mapping of item ID to list of scores.
        metric_averages: Per-metric average value across all items.
        total_items: Number of items evaluated.
        total_duration_ms: Total wall-clock time for the entire run.
    """

    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    dataset_id: uuid.UUID | None = None
    item_scores: dict[str, list[Score]] = field(default_factory=dict)
    metric_averages: dict[str, float] = field(default_factory=dict)
    total_items: int = 0
    total_duration_ms: float = 0.0


class Timer:
    """Simple context-manager timer that records wall-clock elapsed time in ms."""

    def __init__(self) -> None:
        self.elapsed_ms: float = 0.0
        self._start: float = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
