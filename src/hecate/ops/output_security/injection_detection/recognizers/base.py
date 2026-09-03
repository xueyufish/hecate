"""Recognizer base class and finding dataclass for 9.1a injection detection.

The shape mirrors ``DLPRecognizer`` from ``services/security/dlp/recognizer.py``
for operator symmetry.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectionFinding:
    """A single injection pattern detection emitted by a recognizer.

    Attributes:
        entity_type: Canonical entity name (e.g. ``"CODE_PYTHON_INJECTION"``).
        value: The matched substring from the scanned text.
        start: Inclusive character offset of the match in the original text.
        end: Exclusive character offset of the match in the original text.
        score: Confidence score in ``[0.0, 1.0]``. ``1.0`` for deterministic
            regex matches.
        recognizer: Recognizer id that emitted this finding (e.g. ``"code_python"``).
    """

    entity_type: str
    value: str
    start: int
    end: int
    score: float
    recognizer: str


class Recognizer(ABC):
    """Abstract base class for injection-type recognizers.

    Built-in recognizers expose at least three deterministic regex patterns.
    Custom recognizers (loaded from ``guardrail_config["injection_detection"]
    ["custom_patterns"]``) are also expressed as subclasses of this contract.
    """

    @property
    @abstractmethod
    def id(self) -> str:
        """Recognizer id used in finding metadata and rule_name conventions."""

    @property
    @abstractmethod
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        """Compiled regex patterns. At least three patterns required."""

    @property
    def severity(self) -> str:
        """Default severity for findings emitted by this recognizer."""
        return "high"

    def detect(self, content: str, *, sink: str | None = None) -> list[InjectionFinding]:
        """Return all findings in ``content``.

        Args:
            content: The LLM response text to scan.
            sink: Reserved for future sink-aware extension (see
                ``injection-detection/spec.md`` §Future sink-aware extension).
                Current implementation ignores ``sink``; all logic is
                sink-agnostic, content-only.

        Returns:
            A list of findings. Empty list when no pattern matches.
        """
        if not isinstance(content, str) or not content:
            return []
        findings: list[InjectionFinding] = []
        for pattern in self.patterns:
            for match in pattern.finditer(content):
                findings.append(
                    InjectionFinding(
                        entity_type=self.entity_type,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        score=1.0,
                        recognizer=self.id,
                    )
                )
        return findings

    @property
    @abstractmethod
    def entity_type(self) -> str:
        """Canonical entity name for findings emitted by this recognizer."""


def make_recognizer_from_custom_pattern(
    *,
    entity_type: str,
    pattern: str,
    severity: str,
    recognizer_id: str,
) -> Recognizer:
    """Build a one-shot ``Recognizer`` from a single user-supplied regex string."""

    compiled = re.compile(pattern)

    class _CustomRecognizer(Recognizer):
        @property
        def id(self) -> str:
            return recognizer_id

        @property
        def entity_type(self) -> str:
            return entity_type

        @property
        def patterns(self) -> tuple[re.Pattern[str], ...]:
            return (compiled,)

        @property
        def severity(self) -> str:
            return severity

    return _CustomRecognizer()
