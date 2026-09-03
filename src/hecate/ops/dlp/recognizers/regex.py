"""Regex-based DLP recognizer.

Ports the five PII patterns previously hard-coded in
:mod:`hecate.runtime.security.anonymizer` (email, phone, credit_card,
ssn, ip_address), adds Luhn checksum validation for credit cards so we
don't false-positive on arbitrary 16-digit sequences, and adds a
Chinese national ID card pattern that the original module did not
cover.

Entity-type names follow the canonical uppercase form used by the rest
of the DLP subsystem (``EMAIL``, ``PHONE``, ``CREDIT_CARD``, ``SSN``,
``IP_ADDRESS``, ``CHINA_ID_CARD``).
"""

from __future__ import annotations

import re

from hecate.ops.dlp.recognizer import DLPRecognizer
from hecate.ops.dlp.result import DLPFinding

_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "PHONE": re.compile(r"\b(?:\+?1[-.]?)?\(?[0-9]{3}\)?[-.]?[0-9]{3}[-.]?[0-9]{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "CHINA_ID_CARD": re.compile(r"\b\d{17}[\dXx]\b"),
}


def _luhn_check(digits: str) -> bool:
    """Return True iff ``digits`` passes the Luhn checksum.

    Accepts any digit string. Callers should strip separators first.
    """
    total = 0
    for i, char in enumerate(reversed(digits)):
        n = int(char)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class RegexRecognizer(DLPRecognizer):
    """Deterministic regex-based PII detector with Luhn credit-card check."""

    name = "regex_pii"
    supported_entities: list[str] = list(_PATTERNS.keys())

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        findings: list[DLPFinding] = []
        for entity_type, pattern in _PATTERNS.items():
            if entities is not None and entity_type not in entities:
                continue
            for match in pattern.finditer(text):
                value = match.group()
                if entity_type == "CREDIT_CARD" and not self._valid_credit_card(value):
                    continue
                findings.append(
                    DLPFinding(
                        entity_type=entity_type,
                        value=value,
                        start=match.start(),
                        end=match.end(),
                        score=1.0,
                        recognizer=self.name,
                    )
                )
        return findings

    @staticmethod
    def _valid_credit_card(value: str) -> bool:
        """Strip separators and run the Luhn check."""
        digits = value.replace("-", "").replace(" ", "")
        return _luhn_check(digits)
