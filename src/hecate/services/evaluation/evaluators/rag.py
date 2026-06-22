"""RAG evaluators backed by Ragas metrics (moved from rag_evaluators.py).

Provides four evaluators that wrap Ragas metrics for RAG pipeline quality,
registered via ``@register_evaluator``. All Ragas imports are lazy.
"""

from __future__ import annotations

import logging

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.registry import register_evaluator
from hecate.services.evaluation.types import (
    EvalInput,
    EvalOutput,
    Score,
    Timer,
)

logger = logging.getLogger(__name__)

_RAGAS_IMPORT_ERROR_MSG = "ragas is required for RAG evaluators. Install with: pip install hecate[rag]"


@register_evaluator("context_precision")
class ContextPrecisionEvaluator(Evaluator):
    """Measure whether relevant items in retrieved context are ranked higher."""

    category = "result"

    @property
    def name(self) -> str:
        return "context_precision"

    @property
    def description(self) -> str:
        return "Measures whether relevant items in retrieved context are ranked higher"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if input.expected_answer is None:
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning="No expected_answer provided",
                    source="llm_judge",
                )
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)

            try:
                from ragas.dataset_schema import SingleTurnSample
                from ragas.metrics import ContextPrecision

                sample = SingleTurnSample(
                    user_input=input.query,
                    response=input.generated_answer,
                    reference=input.expected_answer,
                    retrieved_contexts=input.retrieved_contexts,
                )
                metric = ContextPrecision()
                result = await metric.single_turn_ascore(sample)
                score = Score(
                    metric_name=self.name,
                    value=float(result),
                    source="llm_judge",
                )
            except ImportError:
                raise ImportError(_RAGAS_IMPORT_ERROR_MSG) from None
            except Exception as e:
                logger.error("ContextPrecision evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"Ragas error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("context_recall")
class ContextRecallEvaluator(Evaluator):
    """Measure whether retrieved context aligns with expected answer."""

    category = "result"

    @property
    def name(self) -> str:
        return "context_recall"

    @property
    def description(self) -> str:
        return "Measures whether retrieved context aligns with the expected answer"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            try:
                from ragas.dataset_schema import SingleTurnSample
                from ragas.metrics import ContextRecall

                sample = SingleTurnSample(
                    user_input=input.query,
                    response=input.generated_answer,
                    reference=input.expected_answer or "",
                    retrieved_contexts=input.retrieved_contexts,
                )
                metric = ContextRecall()
                result = await metric.single_turn_ascore(sample)
                score = Score(
                    metric_name=self.name,
                    value=float(result),
                    source="llm_judge",
                )
            except ImportError:
                raise ImportError(_RAGAS_IMPORT_ERROR_MSG) from None
            except Exception as e:
                logger.error("ContextRecall evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"Ragas error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("faithfulness")
class FaithfulnessEvaluator(Evaluator):
    """Measure whether generated answer is grounded in retrieved context."""

    category = "result"

    @property
    def name(self) -> str:
        return "faithfulness"

    @property
    def description(self) -> str:
        return "Measures whether generated answer is factually consistent with retrieved context"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            try:
                from ragas.dataset_schema import SingleTurnSample
                from ragas.metrics import Faithfulness

                sample = SingleTurnSample(
                    user_input=input.query,
                    response=input.generated_answer,
                    retrieved_contexts=input.retrieved_contexts,
                )
                metric = Faithfulness()
                result = await metric.single_turn_ascore(sample)
                score = Score(
                    metric_name=self.name,
                    value=float(result),
                    source="llm_judge",
                )
            except ImportError:
                raise ImportError(_RAGAS_IMPORT_ERROR_MSG) from None
            except Exception as e:
                logger.error("Faithfulness evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"Ragas error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("answer_relevancy")
class AnswerRelevancyEvaluator(Evaluator):
    """Measure how relevant the generated answer is to the user's query."""

    category = "result"

    @property
    def name(self) -> str:
        return "answer_relevancy"

    @property
    def description(self) -> str:
        return "Measures how relevant the generated answer is to the user's query"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            try:
                from ragas.dataset_schema import SingleTurnSample
                from ragas.metrics import AnswerRelevancy

                sample = SingleTurnSample(
                    user_input=input.query,
                    response=input.generated_answer,
                )
                metric = AnswerRelevancy()
                result = await metric.single_turn_ascore(sample)
                score = Score(
                    metric_name=self.name,
                    value=float(result),
                    source="llm_judge",
                )
            except ImportError:
                raise ImportError(_RAGAS_IMPORT_ERROR_MSG) from None
            except Exception as e:
                logger.error("AnswerRelevancy evaluation failed: %s", e)
                score = Score(
                    metric_name=self.name,
                    value=-1.0,
                    reasoning=f"Ragas error: {e}",
                    source="llm_judge",
                )

        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
