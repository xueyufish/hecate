"""Dictionary-based DLP recognizer.

Looks up exact whole-word matches from a configured term list. By
default matching is case-insensitive; pass ``case_sensitive=True`` for
strict byte-level equality. Terms are anchored with ``(?<!\\w)`` and
``(?!\\w)`` lookarounds so ``"John"`` does not substring-match
``"Johnny"``, but does match against ``"John Smith"``, ``"Mr. John"``,
or ``"Marked.SECRET.doc"``.

Lookaround-anchored boundaries (rather than ``\\b``) keep the closing
boundary working when a term ends with a non-word character such as
``"foo(bar)"``.

The recognizer is designed for low-cardinality lookup tables such as
project codenames, partner names, internal classification labels, or
the de-anonymization allow-list used by audit mode (see
``data-security`` spec).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from hecate.services.security.dlp.recognizer import DLPRecognizer
from hecate.services.security.dlp.result import DLPFinding


class DictionaryRecognizer(DLPRecognizer):
    """Match a fixed list of terms against the scanned text."""

    def __init__(
        self,
        terms: Iterable[str],
        *,
        name: str = "dictionary",
        entity_type: str = "DICTIONARY",
        case_sensitive: bool = False,
    ) -> None:
        self.name = name
        self._entity_type = entity_type
        self.supported_entities = [entity_type]
        self._case_sensitive = case_sensitive
        self._term_count = 0
        escaped = [re.escape(term) for term in terms if term]
        self._term_count = len(escaped)
        if not escaped:
            self._pattern: re.Pattern[str] | None = None
            return
        flags = 0 if case_sensitive else re.IGNORECASE
        self._pattern = re.compile(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)", flags)

    @property
    def case_sensitive(self) -> bool:
        return self._case_sensitive

    @property
    def term_count(self) -> int:
        return self._term_count

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        if self._pattern is None:
            return []
        if entities is not None and self._entity_type not in entities:
            return []
        findings: list[DLPFinding] = []
        for match in self._pattern.finditer(text):
            findings.append(
                DLPFinding(
                    entity_type=self._entity_type,
                    value=match.group(),
                    start=match.start(),
                    end=match.end(),
                    score=1.0,
                    recognizer=self.name,
                )
            )
        return findings
