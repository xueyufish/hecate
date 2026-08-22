"""Python code injection recognizer (9.1a)."""

from __future__ import annotations

import re

from hecate.services.security.output.injection_detection.recognizers.base import Recognizer


class CodePythonRecognizer(Recognizer):
    """Detect Python code-injection primitives in LLM output."""

    @property
    def id(self) -> str:
        return "code_python"

    @property
    def entity_type(self) -> str:
        return "CODE_PYTHON_INJECTION"

    @property
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        return (
            re.compile(r"\beval\s*\("),
            re.compile(r"\bexec\s*\("),
            re.compile(r"\b__import__\s*\("),
            re.compile(r"\bcompile\s*\([^)]*['\"]"),
            re.compile(r"\bsubprocess\.(?:call|run|Popen)\s*\([^)]*\+"),
            re.compile(r"\bos\.system\s*\([^)]*\+"),
            re.compile(r"\bpickle\.loads?\s*\("),
        )
