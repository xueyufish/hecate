"""Jinja template injection recognizer (9.1a)."""

from __future__ import annotations

import re

from hecate.services.security.output.injection_detection.recognizers.base import Recognizer


class TemplateJinjaRecognizer(Recognizer):
    """Detect Jinja SSTI primitives in LLM output."""

    @property
    def id(self) -> str:
        return "template_jinja"

    @property
    def entity_type(self) -> str:
        return "TEMPLATE_INJECTION"

    @property
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        return (
            re.compile(r"\{\{\s*config\b"),
            re.compile(r"\{\%\s*import\b"),
            re.compile(r"\{\%\s*include\b"),
            re.compile(r"\{\{\s*self\.__class__"),
            re.compile(r"\{\{\s*\w+\.__class__\.__init__\.__globals__"),
            re.compile(r"\{\{\s*lipsum\.__globals__"),
        )
