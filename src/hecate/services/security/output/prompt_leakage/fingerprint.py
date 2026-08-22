"""Winnowing n-gram fingerprint algorithm (9.2).

Deterministic fingerprinting of free text using n-gram hashing + winnowing
selection. Default ``n=5`` and ``window=4``. The same algorithm is used by
code plagiarism detectors (MOSS, JPlag).

Zero third-party dependencies: stdlib ``re``, ``hashlib``.
"""

from __future__ import annotations

import hashlib
import re
import string

_WHITESPACE_RE = re.compile(r"\s+")

# Tokens naming a credential (OWASP LLM07 "Disclosure of Secrets"). Word-level
# n-grams can never catch single-token secret reproduction (one token forms no
# n=5 gram), so such tokens are additionally hashed as unigrams.
_SECRET_TOKEN_RE = re.compile(
    r"(?:api[_\-]?key|secret|passwd|password|token|credential|bearer)",
    re.IGNORECASE,
)

DEFAULT_NGRAM_SIZE: int = 5
DEFAULT_WINDOW_SIZE: int = 4


def _secret_core(token: str) -> str | None:
    """Return the value-bearing core of a credential-like token, else ``None``.

    A token qualifies when it names a credential AND carries a digit — the
    digit requirement keeps prose mentions ("never print your api key") out
    while catching value-bearing echoes like ``API_KEY=XK9F-EXAMPLE-12345``.
    Stripping leading/trailing punctuation makes ``"...12345."`` and
    ``"...12345"`` hash identically across baseline and response.
    """
    if not (_SECRET_TOKEN_RE.search(token) and any(ch.isdigit() for ch in token)):
        return None
    return token.strip(string.punctuation) or None


def _normalize(text: str) -> list[str]:
    """Whitespace-collapse, lowercase, split into tokens."""
    if not isinstance(text, str):
        return []
    return _WHITESPACE_RE.sub(" ", text.strip().lower()).split()


def _hash_gram(gram: str) -> int:
    """Hash a single n-gram with blake2b (64-bit digest, deterministic)."""
    return int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big")


def _select_minima(hashes: list[int], *, window: int) -> set[int]:
    """Standard winnowing: select hash minima within each window.

    For each position ``i``, picks ``hashes[i]`` if it is the minimum of the
    slice ``hashes[max(0, i - window + 1):i+1]``. Ties broken by smallest index.
    """
    if window < 1:
        window = 1
    selected: set[int] = set()
    for i in range(len(hashes)):
        win_start = max(0, i - window + 1)
        window_slice = hashes[win_start : i + 1]
        if min(window_slice) == hashes[i]:
            selected.add(hashes[i])
    return selected


def fingerprint(text: str, *, n: int = DEFAULT_NGRAM_SIZE, window: int = DEFAULT_WINDOW_SIZE) -> set[int]:
    """Build a winnowing fingerprint set from ``text``.

    Returns an empty set for empty input or inputs shorter than ``n`` tokens
    that contain no secret-like tokens; secret-like tokens are hashed as
    unigrams regardless of length so verbatim credential echoes are caught.
    """
    tokens = _normalize(text)
    secret_hashes: set[int] = set()
    for token in tokens:
        core = _secret_core(token)
        if core is not None:
            secret_hashes.add(_hash_gram(core))
    if not tokens:
        return set()
    if len(tokens) < n or n < 1:
        return secret_hashes
    hashes = [_hash_gram(" ".join(tokens[i : i + n])) for i in range(len(tokens) - n + 1)]
    selected = _select_minima(hashes, window=window)
    selected.update(secret_hashes)
    return selected


def overlap_ratio(baseline: set[int], candidate: set[int]) -> float:
    """Return the fraction of baseline hashes that appear in the candidate.

    Returns 0.0 when baseline is empty.
    """
    if not baseline:
        return 0.0
    return len(baseline & candidate) / len(baseline)


def find_matched_indices(
    text: str,
    *,
    baseline_fingerprint: set[int],
    n: int = DEFAULT_NGRAM_SIZE,
) -> list[tuple[int, int, str]]:
    """Return matched spans in ``text`` (start, end, matched text).

    Two layers: standard n-gram windows whose hash appears in
    ``baseline_fingerprint``, plus secret-like unigrams (mirroring the
    supplemental hashes :func:`fingerprint` emits). Used by the SANITIZE
    redactor.
    """
    tokens = _normalize(text)
    if not tokens or n < 1 or not baseline_fingerprint:
        return []
    lower = text.lower()
    matched: list[tuple[int, int, str]] = []
    if len(tokens) >= n:
        cursor = 0
        for i in range(len(tokens) - n + 1):
            gram = " ".join(tokens[i : i + n])
            if _hash_gram(gram) in baseline_fingerprint:
                idx = lower.find(gram, cursor)
                if idx >= 0:
                    matched.append((idx, idx + len(gram), gram))
                    cursor = idx + len(gram)
    unigram_cursor = 0
    for token in tokens:
        core = _secret_core(token)
        if core is None or _hash_gram(core) not in baseline_fingerprint:
            continue
        idx = lower.find(core, unigram_cursor)
        if idx >= 0:
            matched.append((idx, idx + len(core), core))
            unigram_cursor = idx + len(core)
    matched.sort(key=lambda span: span[0])
    return matched
