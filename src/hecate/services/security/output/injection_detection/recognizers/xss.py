"""XSS (HTML/JS injection) recognizer (9.1a)."""

from __future__ import annotations

import re

from hecate.services.security.output.injection_detection.recognizers.base import Recognizer


class XssRecognizer(Recognizer):
    """Detect HTML/JS execution primitives in LLM output."""

    @property
    def id(self) -> str:
        return "xss"

    @property
    def entity_type(self) -> str:
        return "XSS_INJECTION"

    @property
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        return (
            re.compile(r"<\s*script\b", re.IGNORECASE),
            re.compile(r"\bonerror\s*=", re.IGNORECASE),
            re.compile(r"\bonload\s*=", re.IGNORECASE),
            re.compile(r"\bjavascript\s*:", re.IGNORECASE),
            re.compile(r"<\s*iframe\b", re.IGNORECASE),
            re.compile(r"<\s*svg\s+onload\b", re.IGNORECASE),
            re.compile(r"<\s*img[^>]+onerror\s*=", re.IGNORECASE),
        )
