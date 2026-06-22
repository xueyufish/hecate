"""Abstract base class for all evaluators.

Every evaluator — whether RAG-specific, Agent-specific, or custom — must
inherit from :class:`Evaluator` and implement the :meth:`evaluate` method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hecate.services.evaluation.types import EvalInput, EvalOutput, LLMConfig


class Evaluator(ABC):
    """Abstract base class for evaluation metrics.

    Subclasses must define:

    - :pyattr:`name` — short identifier for the metric (e.g. ``"faithfulness"``)
    - :pyattr:`description` — human-readable explanation
    - :pymeth:`evaluate` — async evaluation logic

    Each evaluator optionally accepts an :class:`LLMConfig` to control which
    model is used for LLM-as-Judge or Ragas-backed metrics.

    Args:
        llm_config: Optional per-evaluator LLM configuration. When ``None``,
            evaluators should fall back to a default model.
    """

    category: str = "generic"

    def __init__(self, llm_config: LLMConfig | None = None) -> None:
        self.llm_config = llm_config

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this evaluator metric."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this evaluator measures."""
        ...

    @abstractmethod
    async def evaluate(self, input: EvalInput) -> EvalOutput:
        """Evaluate a single item and return metric scores.

        Args:
            input: The evaluation input (query, contexts, answer, etc.).

        Returns:
            An :class:`EvalOutput` containing one or more :class:`Score` objects.
        """
        ...
