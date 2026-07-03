"""EvaluatorABC — abstract interface for evaluation plugins.

All evaluators — built-in or third-party — must implement this interface
to be registered with the PluginRegistry under type="evaluator".
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hecate.services.evaluation.types import EvalInput, EvalOutput


class EvaluatorABC(ABC):
    """Abstract base class for evaluation plugins.

    Subclasses must define:

    - :pyattr:`name` — short identifier for the metric (e.g. ``"faithfulness"``)
    - :pyattr:`description` — human-readable explanation
    - :pymeth:`evaluate` — async evaluation logic

    Example::

        class MyEvaluator(EvaluatorABC):
            @property
            def name(self) -> str:
                return "my_metric"

            @property
            def description(self) -> str:
                return "My custom evaluation metric"

            async def evaluate(self, input: EvalInput) -> EvalOutput:
                return EvalOutput(scores=[Score(...)])
    """

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
