"""LLM Guard scanner for input/output safety checks.

Provides prompt and output scanning using LLM Guard with:
- Prompt injection detection
- PII anonymization
- Secret detection
- Toxicity detection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from hecate.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result from a safety scan."""

    is_safe: bool
    score: float
    issues: list[str]


class LLMGuardScanner:
    """Scan prompts and outputs for safety issues.

    Uses LLM Guard for:
    - Prompt injection detection (DeBERTa-v3)
    - PII anonymization (Presidio + BERT NER)
    - Secret detection (detect-secrets)
    - Toxicity detection
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled and settings.LLM_GUARD_ENABLED
        self._prompt_scanners = None
        self._output_scanners = None

    def _get_prompt_scanners(self):
        """Lazy load prompt scanners."""
        if self._prompt_scanners is None:
            try:
                from llm_guard.input_scanners import (
                    Anonymize,
                    PromptInjection,
                    Secrets,
                )

                self._prompt_scanners = [
                    PromptInjection(threshold=0.5),
                    Anonymize(),
                    Secrets(),
                ]
                logger.info("Loaded LLM Guard prompt scanners")
            except ImportError:
                logger.warning("llm_guard not installed. Using mock scanners.")
                self._prompt_scanners = "mock"
        return self._prompt_scanners

    def _get_output_scanners(self):
        """Lazy load output scanners."""
        if self._output_scanners is None:
            try:
                from llm_guard.output_scanners import Toxicity

                self._output_scanners = [
                    Toxicity(threshold=0.7),
                ]
                logger.info("Loaded LLM Guard output scanners")
            except ImportError:
                logger.warning("llm_guard not installed. Using mock scanners.")
                self._output_scanners = "mock"
        return self._output_scanners

    async def scan_prompt(self, prompt: str) -> ScanResult:
        """Scan a prompt for safety issues.

        Args:
            prompt: The user prompt to scan.

        Returns:
            ScanResult with safety assessment.
        """
        if not self.enabled:
            return ScanResult(is_safe=True, score=1.0, issues=[])

        scanners = self._get_prompt_scanners()

        if scanners == "mock":
            return self._mock_scan(prompt)

        issues = []
        for scanner in scanners:
            try:
                sanitized, is_valid, risk_score = scanner.scan(prompt)
                if not is_valid:
                    issues.append(f"{scanner.__class__.__name__}: risk_score={risk_score:.2f}")
            except Exception as e:
                logger.warning(f"Scanner {scanner.__class__.__name__} failed: {e}")

        return ScanResult(
            is_safe=len(issues) == 0,
            score=1.0 - len(issues) * 0.3,
            issues=issues,
        )

    async def scan_output(self, output: str, prompt: str = "") -> ScanResult:
        """Scan an output for safety issues.

        Args:
            output: The LLM output to scan.
            prompt: The original prompt (for context).

        Returns:
            ScanResult with safety assessment.
        """
        if not self.enabled:
            return ScanResult(is_safe=True, score=1.0, issues=[])

        scanners = self._get_output_scanners()

        if scanners == "mock":
            return self._mock_scan(output)

        issues = []
        for scanner in scanners:
            try:
                sanitized, is_valid, risk_score = scanner.scan(output)
                if not is_valid:
                    issues.append(f"{scanner.__class__.__name__}: risk_score={risk_score:.2f}")
            except Exception as e:
                logger.warning(f"Scanner {scanner.__class__.__name__} failed: {e}")

        return ScanResult(
            is_safe=len(issues) == 0,
            score=1.0 - len(issues) * 0.3,
            issues=issues,
        )

    def _mock_scan(self, text: str) -> ScanResult:
        """Mock scan for testing."""
        issues = []
        if "hack" in text.lower() or "exploit" in text.lower():
            issues.append("Suspicious content detected")
        return ScanResult(
            is_safe=len(issues) == 0,
            score=1.0 - len(issues) * 0.5,
            issues=issues,
        )


llm_guard_scanner = LLMGuardScanner()
