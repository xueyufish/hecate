"""Deterministic format evaluators — pure Python, no LLM calls."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter

from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.registry import register_evaluator
from hecate.services.evaluation.types import EvalInput, EvalOutput, Score, Timer

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    return text.lower().strip().split()


def _lcs_length(a: list[str], b: list[str]) -> int:
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if a[i - 1] == b[j - 1] else max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


@register_evaluator("exact_match")
class ExactMatchEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "exact_match"

    @property
    def description(self) -> str:
        return "Checks if generated answer exactly matches expected answer (case-insensitive)"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if not input.expected_answer:
                score = Score(metric_name=self.name, value=-1.0, reasoning="No expected_answer", source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            match = input.generated_answer.strip().lower() == input.expected_answer.strip().lower()
            score = Score(metric_name=self.name, value=1.0 if match else 0.0, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("contains")
class ContainsEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "contains"

    @property
    def description(self) -> str:
        return "Checks if generated answer contains a specified substring"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            substring = input.metadata.get("expected_substring", "")
            found = substring in input.generated_answer if substring else False
            score = Score(metric_name=self.name, value=1.0 if found else 0.0, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("contains_any")
class ContainsAnyEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "contains_any"

    @property
    def description(self) -> str:
        return "Checks if generated answer contains any of the specified substrings"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            substrings: list[str] = input.metadata.get("expected_substrings", [])
            found = any(s in input.generated_answer for s in substrings) if substrings else False
            score = Score(metric_name=self.name, value=1.0 if found else 0.0, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("regex_match")
class RegexMatchEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "regex_match"

    @property
    def description(self) -> str:
        return "Checks if generated answer matches a regex pattern"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            pattern = input.metadata.get("regex_pattern", "")
            matched = bool(re.search(pattern, input.generated_answer)) if pattern else False
            score = Score(metric_name=self.name, value=1.0 if matched else 0.0, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("is_json")
class IsJSONEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "is_json"

    @property
    def description(self) -> str:
        return "Validates that generated answer is valid JSON"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            try:
                json.loads(input.generated_answer)
                score = Score(metric_name=self.name, value=1.0, source="deterministic")
            except (json.JSONDecodeError, TypeError):
                score = Score(metric_name=self.name, value=0.0, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("format_check")
class FormatCheckEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "format_check"

    @property
    def description(self) -> str:
        return "Validates output format against a schema (required keys in JSON)"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            schema = input.metadata.get("schema", {})
            required_keys = schema.get("required", [])
            try:
                parsed = json.loads(input.generated_answer)
                if isinstance(parsed, dict):
                    missing = [k for k in required_keys if k not in parsed]
                    score = Score(
                        metric_name=self.name,
                        value=1.0 if not missing else 0.0,
                        reasoning=f"Missing keys: {missing}" if missing else None,
                        source="deterministic",
                    )
                else:
                    score = Score(
                        metric_name=self.name,
                        value=0.0,
                        reasoning="Not a JSON object",
                        source="deterministic",
                    )
            except (json.JSONDecodeError, TypeError) as e:
                score = Score(metric_name=self.name, value=-1.0, reasoning=f"Parse error: {e}", source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("bleu_score")
class BLEUScoreEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "bleu_score"

    @property
    def description(self) -> str:
        return "Computes BLEU-4 score between generated and expected answers"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if not input.expected_answer:
                score = Score(metric_name=self.name, value=-1.0, reasoning="No expected_answer", source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            gen_tokens = _tokenize(input.generated_answer)
            exp_tokens = _tokenize(input.expected_answer)
            if not gen_tokens or not exp_tokens:
                score = Score(metric_name=self.name, value=0.0, source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            precisions: list[float] = []
            for n in range(1, 5):
                gen_ngrams = Counter(tuple(gen_tokens[i : i + n]) for i in range(len(gen_tokens) - n + 1))
                exp_ngrams = Counter(tuple(exp_tokens[i : i + n]) for i in range(len(exp_tokens) - n + 1))
                if not gen_ngrams:
                    precisions.append(0.0)
                    continue
                matches = sum(min(gen_ngrams[g], exp_ngrams.get(g, 0)) for g in gen_ngrams)
                precisions.append(matches / sum(gen_ngrams.values()))
            import math

            if min(precisions) > 0:
                bp = 1.0 if len(gen_tokens) >= len(exp_tokens) else math.exp(1 - len(exp_tokens) / len(gen_tokens))
                bleu = bp * math.exp(sum(math.log(p) for p in precisions) / 4)
            else:
                bleu = 0.0
            score = Score(metric_name=self.name, value=min(bleu, 1.0), source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("rouge_score")
class ROUGEScoreEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "rouge_score"

    @property
    def description(self) -> str:
        return "Computes ROUGE-L F1 score between generated and expected answers"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if not input.expected_answer:
                score = Score(metric_name=self.name, value=-1.0, reasoning="No expected_answer", source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            gen_tokens = _tokenize(input.generated_answer)
            exp_tokens = _tokenize(input.expected_answer)
            if not gen_tokens or not exp_tokens:
                score = Score(metric_name=self.name, value=0.0, source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            lcs = _lcs_length(gen_tokens, exp_tokens)
            precision = lcs / len(gen_tokens) if gen_tokens else 0.0
            recall = lcs / len(exp_tokens) if exp_tokens else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            score = Score(metric_name=self.name, value=f1, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)


@register_evaluator("f1_score")
class F1ScoreEvaluator(Evaluator):
    category = "result"

    @property
    def name(self) -> str:
        return "f1_score"

    @property
    def description(self) -> str:
        return "Computes token-level F1 score between generated and expected answers"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        with Timer() as timer:
            if not input.expected_answer:
                score = Score(metric_name=self.name, value=-1.0, reasoning="No expected_answer", source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            gen_tokens = set(_tokenize(input.generated_answer))
            exp_tokens = set(_tokenize(input.expected_answer))
            if not gen_tokens or not exp_tokens:
                score = Score(metric_name=self.name, value=0.0, source="deterministic")
                return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
            common = gen_tokens & exp_tokens
            precision = len(common) / len(gen_tokens)
            recall = len(common) / len(exp_tokens)
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            score = Score(metric_name=self.name, value=f1, source="deterministic")
        return EvalOutput(scores=[score], duration_ms=timer.elapsed_ms)
