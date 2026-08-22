"""Sanitize redactor for 9.2 prompt leakage matches.

Replaces matched n-gram windows + 5-token context on each side with
``<REDACTED>``. Conservative: whole matched windows are replaced so that
partial leaks cannot survive the rewrite.
"""

from __future__ import annotations

from hecate.services.security.output.prompt_leakage.fingerprint import (
    DEFAULT_NGRAM_SIZE,
    find_matched_indices,
)

_REDACTION_MARKER = "<REDACTED>"
_CONTEXT_TOKENS = 5


def redact(content: str, *, baseline_fingerprint: set[int], n: int = DEFAULT_NGRAM_SIZE) -> str:
    """Return ``content`` with matched n-grams (and their context) redacted."""
    if not isinstance(content, str) or not content:
        return content
    matches = find_matched_indices(content, baseline_fingerprint=baseline_fingerprint, n=n)
    if not matches:
        return content

    tokens = content.split()
    spans: list[tuple[int, int]] = []
    for char_start, _char_end, _gram in matches:
        prefix = content[:char_start]
        prefix_tokens = len(prefix.split())
        ngram_token_start = prefix_tokens
        ngram_token_end = ngram_token_start + n
        token_start = max(0, ngram_token_start - _CONTEXT_TOKENS)
        token_end = min(len(tokens), ngram_token_end + _CONTEXT_TOKENS)
        char_start_with_ctx = len(" ".join(tokens[:token_start]))
        if token_start > 0:
            char_start_with_ctx += 1
        char_end_with_ctx = len(" ".join(tokens[:token_end]))
        spans.append((char_start_with_ctx, char_end_with_ctx))

    if not spans:
        return content

    out: list[str] = []
    cursor = 0
    spans.sort()
    for start, end in spans:
        if start < cursor:
            continue
        out.append(content[cursor:start])
        out.append(_REDACTION_MARKER)
        cursor = end
    out.append(content[cursor:])
    return "".join(out)
