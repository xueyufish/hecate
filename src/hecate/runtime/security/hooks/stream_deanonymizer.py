"""Buffer-based stream deanonymizer for safe PII restoration in streaming responses."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class StreamDeanonymizer:
    """Buffers streaming tokens to deanonymize complete PII placeholders.

    Tokens that don't start with '[' are emitted immediately. When '[' is
    detected, buffering begins until ']' closes the placeholder. Complete
    placeholders are looked up in the PII mappings; matched ones are
    replaced with the original value, unmatched ones pass through.
    """

    def __init__(self, mappings: dict[str, str] | None = None) -> None:
        self._mappings = mappings or {}
        self._buffer = ""
        self._in_placeholder = False

    @property
    def mappings(self) -> dict[str, str]:
        return self._mappings

    @mappings.setter
    def mappings(self, value: dict[str, str]) -> None:
        self._mappings = value

    def process(self, token: str) -> str:
        """Process a single token, returning text safe to emit.

        Non-PII text is returned immediately. PII placeholders are buffered
        until complete, then deanonymized.
        """
        if not self._in_placeholder and "[" not in token:
            return token

        result = ""
        for char in token:
            if not self._in_placeholder:
                if char == "[":
                    self._in_placeholder = True
                    self._buffer = "["
                else:
                    result += char
            else:
                self._buffer += char
                if char == "]":
                    self._in_placeholder = False
                    result += self._try_deanonymize(self._buffer)
                    self._buffer = ""

        return result

    def flush(self) -> str:
        """Flush any remaining buffered content.

        Complete placeholders are deanonymized; partial buffers are emitted
        as-is since they cannot be resolved.
        """
        if not self._buffer:
            return ""

        buffered = self._buffer
        self._buffer = ""
        self._in_placeholder = False

        if buffered.endswith("]"):
            return self._try_deanonymize(buffered)
        return buffered

    def _try_deanonymize(self, placeholder: str) -> str:
        """Look up a placeholder in mappings; return original or pass through."""
        reversed_mappings = {v: k for k, v in self._mappings.items()}
        if placeholder in reversed_mappings:
            return reversed_mappings[placeholder]
        return placeholder
