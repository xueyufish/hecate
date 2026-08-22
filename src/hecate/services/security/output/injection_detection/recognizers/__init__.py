"""Recognizer base + built-in injection pattern recognizers."""

from __future__ import annotations

from hecate.services.security.output.injection_detection.recognizers.base import (
    InjectionFinding,
    Recognizer,
)
from hecate.services.security.output.injection_detection.recognizers.code_python import (
    CodePythonRecognizer,
)
from hecate.services.security.output.injection_detection.recognizers.sql_injection import (
    SqlInjectionRecognizer,
)
from hecate.services.security.output.injection_detection.recognizers.template_jinja import (
    TemplateJinjaRecognizer,
)
from hecate.services.security.output.injection_detection.recognizers.xss import (
    XssRecognizer,
)

BUILTIN_RECOGNIZERS: tuple[type[Recognizer], ...] = (
    CodePythonRecognizer,
    SqlInjectionRecognizer,
    TemplateJinjaRecognizer,
    XssRecognizer,
)

__all__ = [
    "BUILTIN_RECOGNIZERS",
    "CodePythonRecognizer",
    "InjectionFinding",
    "Recognizer",
    "SqlInjectionRecognizer",
    "TemplateJinjaRecognizer",
    "XssRecognizer",
]
