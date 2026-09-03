"""Severity classifier for 9.2 prompt leakage matches.

Heuristic regex classifiers on the matched substring context. Four
categories align with OWASP LLM07:2025 example attack types. Best-effort:
false positives are accepted in exchange for keeping the hot path ML-free.
"""

from __future__ import annotations

import re

# Order matters — first match wins.
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "secrets",
        re.compile(r"\b(api[_\-]?key|secret|password|passwd|token|bearer|credential)\b", re.IGNORECASE),
    ),
    (
        "rules",
        re.compile(
            r"\b(must\s+not|do\s+not|should\s+not|never|rule\s*:|policy\s*:|filter\s*:|forbidden|prohibited)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "roles",
        re.compile(r"(<role>|permission\s*:|role\s*:)", re.IGNORECASE),
    ),
)

_CATEGORY_SEVERITY: dict[str, str] = {
    "secrets": "critical",
    "rules": "high",
    "roles": "high",
    "persona": "low",
}


def classify(matched_substring: str, *, context_window: str | None = None) -> tuple[str, str]:
    """Return ``(category, severity)`` for a matched substring.

    Inspects the matched substring + an optional surrounding context window.
    Falls back to ``("persona", "low")`` when no rule fires.
    """
    haystack = matched_substring if not context_window else f"{context_window}\n{matched_substring}"
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(haystack):
            severity = _CATEGORY_SEVERITY[category]
            return category, severity
    return "persona", _CATEGORY_SEVERITY["persona"]
