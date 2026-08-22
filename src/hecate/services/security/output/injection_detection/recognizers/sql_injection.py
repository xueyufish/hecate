"""SQL injection recognizer (9.1a)."""

from __future__ import annotations

import re

from hecate.services.security.output.injection_detection.recognizers.base import Recognizer


class SqlInjectionRecognizer(Recognizer):
    """Detect SQL DDL/DML hostile statements in LLM output."""

    @property
    def id(self) -> str:
        return "sql_injection"

    @property
    def entity_type(self) -> str:
        return "SQL_INJECTION"

    @property
    def patterns(self) -> tuple[re.Pattern[str], ...]:
        return (
            re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)\b", re.IGNORECASE),
            re.compile(r"\bUNION\s+(?:ALL\s+)?SELECT\b", re.IGNORECASE),
            re.compile(r";\s*DELETE\s+FROM\b", re.IGNORECASE),
            re.compile(r";\s*DROP\s+", re.IGNORECASE),
            re.compile(r"'\s*OR\s+['\"]?1['\"]?\s*=\s*['\"]?1['\"]?", re.IGNORECASE),
            re.compile(r"--\s*$", re.MULTILINE),
            re.compile(r"\bxp_cmdshell\b", re.IGNORECASE),
        )
