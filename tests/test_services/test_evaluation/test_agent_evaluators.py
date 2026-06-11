"""Tests for ToolCallAccuracyEvaluator and TaskCompletionEvaluator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from hecate.services.evaluation.agent_evaluators import (
    TaskCompletionEvaluator,
    ToolCallAccuracyEvaluator,
)
from hecate.services.evaluation.types import EvalInput


class TestToolCallAccuracyEvaluator:
    async def test_no_tool_calls_returns_minus_one(self) -> None:
        evaluator = ToolCallAccuracyEvaluator()
        input_data = EvalInput(query="test", generated_answer="answer")
        output = await evaluator.evaluate(input_data)
        assert len(output.scores) == 1
        assert output.scores[0].value == -1.0
        assert "No tool_calls" in (output.scores[0].reasoning or "")

    async def test_with_tool_calls_calls_llm_judge(self) -> None:
        evaluator = ToolCallAccuracyEvaluator()
        input_data = EvalInput(
            query="search for X",
            generated_answer="found X",
            tool_calls=[{"name": "search", "args": {"query": "X"}}],
        )
        mock_result = {"score": 0.8, "reasoning": "Good tool usage"}
        with patch(
            "hecate.services.evaluation.agent_evaluators._call_llm_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            output = await evaluator.evaluate(input_data)
        assert output.scores[0].value == 0.8
        assert output.scores[0].metric_name == "tool_call_accuracy"

    async def test_llm_judge_failure_returns_minus_one(self) -> None:
        evaluator = ToolCallAccuracyEvaluator()
        input_data = EvalInput(
            query="test",
            generated_answer="answer",
            tool_calls=[{"name": "tool1"}],
        )
        with patch(
            "hecate.services.evaluation.agent_evaluators._call_llm_judge",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API error"),
        ):
            output = await evaluator.evaluate(input_data)
        assert output.scores[0].value == -1.0
        assert "LLM judge error" in (output.scores[0].reasoning or "")

    async def test_name_and_description(self) -> None:
        evaluator = ToolCallAccuracyEvaluator()
        assert evaluator.name == "tool_call_accuracy"
        assert "tool" in evaluator.description.lower()


class TestTaskCompletionEvaluator:
    async def test_basic_completion(self) -> None:
        evaluator = TaskCompletionEvaluator()
        input_data = EvalInput(query="do something", generated_answer="done")
        mock_result = {"score": 0.9, "reasoning": "Task completed well"}
        with patch(
            "hecate.services.evaluation.agent_evaluators._call_llm_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            output = await evaluator.evaluate(input_data)
        assert output.scores[0].value == 0.9
        assert output.scores[0].metric_name == "task_completion"

    async def test_llm_judge_failure(self) -> None:
        evaluator = TaskCompletionEvaluator()
        input_data = EvalInput(query="test", generated_answer="answer")
        with patch(
            "hecate.services.evaluation.agent_evaluators._call_llm_judge",
            new_callable=AsyncMock,
            side_effect=RuntimeError("timeout"),
        ):
            output = await evaluator.evaluate(input_data)
        assert output.scores[0].value == -1.0
        assert "LLM judge error" in (output.scores[0].reasoning or "")

    async def test_name_and_description(self) -> None:
        evaluator = TaskCompletionEvaluator()
        assert evaluator.name == "task_completion"
        assert "task" in evaluator.description.lower()
