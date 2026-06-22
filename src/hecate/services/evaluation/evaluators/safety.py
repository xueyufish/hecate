"""Safety and security LLM-as-Judge evaluators."""

from __future__ import annotations

import logging

from hecate.services.evaluation.evaluators.judge import _make_generic_evaluator
from hecate.services.evaluation.registry import register_evaluator

logger = logging.getLogger(__name__)

PromptInjectionResistanceEvaluator = register_evaluator("prompt_injection_resistance")(
    _make_generic_evaluator("prompt_injection_resistance", "Tests if output resists prompt injection"),
)
PIILeakageDetectionEvaluator = register_evaluator("pii_leakage_detection")(
    _make_generic_evaluator("pii_leakage_detection", "Detects PII leakage in generated output"),
)
JailbreakResistanceEvaluator = register_evaluator("jailbreak_resistance")(
    _make_generic_evaluator("jailbreak_resistance", "Tests resistance to jailbreak attempts"),
)
