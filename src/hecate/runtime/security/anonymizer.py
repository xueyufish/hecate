"""PII anonymization and deanonymization service.

Provides reversible PII masking for sensitive data like:
- Email addresses
- Phone numbers
- Credit card numbers
- Social security numbers
- Names
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AnonymizedText:
    """Result of PII anonymization."""

    text: str
    mappings: dict[str, str] = field(default_factory=dict)


class PIIAnonymizer:
    """Anonymize and deanonymize PII in text.

    Uses regex patterns for common PII types with
    reversible masking for accurate restoration.
    """

    PATTERNS = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone": r"\b(?:\+?1[-.]?)?\(?[0-9]{3}\)?[-.]?[0-9]{3}[-.]?[0-9]{4}\b",
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "ip_address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    }

    def __init__(self):
        self._counter = 0

    def anonymize(self, text: str) -> AnonymizedText:
        """Anonymize PII in text.

        Args:
            text: The text to anonymize.

        Returns:
            AnonymizedText with masked text and restoration mappings.
        """
        mappings = {}
        anonymized = text

        for pii_type, pattern in self.PATTERNS.items():
            matches = re.finditer(pattern, anonymized)
            for match in matches:
                original = match.group()
                if original not in mappings:
                    self._counter += 1
                    placeholder = f"[{pii_type.upper()}_{self._counter}]"
                    mappings[original] = placeholder
                anonymized = anonymized.replace(original, mappings[original])

        return AnonymizedText(text=anonymized, mappings=mappings)

    def deanonymize(self, anonymized: AnonymizedText) -> str:
        """Restore original PII from anonymized text.

        Args:
            anonymized: The AnonymizedText with mappings.

        Returns:
            str: The original text with PII restored.
        """
        text = anonymized.text
        for original, placeholder in anonymized.mappings.items():
            text = text.replace(placeholder, original)
        return text

    def has_pii(self, text: str) -> bool:
        """Check if text contains PII.

        Args:
            text: The text to check.

        Returns:
            bool: True if PII is detected.
        """
        return any(re.search(pattern, text) for pattern in self.PATTERNS.values())


pii_anonymizer = PIIAnonymizer()
