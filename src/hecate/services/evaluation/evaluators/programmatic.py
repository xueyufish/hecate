"""Programmatic code execution evaluators."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.registry import register_evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score, Timer

logger = logging.getLogger(__name__)


@register_evaluator("python_code_eval")
class PythonCodeEvaluator(Evaluator):
    """Executes a user-provided Python function against the evaluation input."""

    category = "generic"

    def __init__(
        self,
        ll_config=None,
        func: Callable[[EvalInput], float] | None = None,
    ) -> None:
        super().__init__(ll_config)
        self._func = func

    @property
    def name(self) -> str:
        return "python_code_eval"

    @property
    def description(self) -> str:
        return "Executes a user-provided Python function against the evaluation input"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if self._func is None:
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning="No function provided",
                    source="deterministic",
                )
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            try:
                result = self._func(input)
                score = Score(
                    metric_name=self.name,
                    value=float(result),
                    source="deterministic",
                )
            except Exception as e:
                logger.error("python_code_eval failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"Execution error: {e}",
                    source="deterministic",
                )
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("custom_callable")
class CustomCallableEvaluator(Evaluator):
    """Wraps an arbitrary async callable as an evaluator."""

    category = "generic"

    def __init__(
        self,
        ll_config=None,
        callable: Callable[[EvalInput], Awaitable[float]] | None = None,
    ) -> None:
        super().__init__(ll_config)
        self._callable_fn = callable

    @property
    def name(self) -> str:
        return "custom_callable"

    @property
    def description(self) -> str:
        return "Wraps an arbitrary async callable as an evaluator"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if self._callable_fn is None:
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning="No callable provided",
                    source="deterministic",
                )
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            try:
                result = await self._callable_fn(input)
                score = Score(
                    metric_name=self.name,
                    value=float(result),
                    source="deterministic",
                )
            except Exception as e:
                logger.error("custom_callable failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"Execution error: {e}",
                    source="deterministic",
                )
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
