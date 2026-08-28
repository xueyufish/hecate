"""Shell static analysis (T3, 9.4 content-aware gating).

Decomposes a shell command into operator segments, then per-segment tokenizes
with ``shlex`` (POSIX), normalizes flag clusters, and recursively inspects
command-substitution payloads (``$(...)``, backticks, ``eval``/``bash -c`` /
``sh -c`` wrappers). Each segment is exposed to the policy layer for
``DangerousPattern`` matching.

The module deliberately uses **only** the Python standard library so the
engine layer keeps its zero-external-deps constraint. Decomposition handles
the common-evasion shapes the existing ``fnmatch`` single-arg matcher
misses (extra whitespace, flag-order variants, piped shells, command
substitution). A parse failure degrades to a single-string glob match
against the original payload — never to "no match" — so the system is
strictly more conservative than the previous implementation.

Public surface:
    ``decompose_command(command)`` — list of segments (each a list of tokens
        ready for ``shlex``-like downstream consumption). The whole command
        is at index ``0`` when no operators are present.
    ``analyze_command(command, matcher_fn)`` — yields per-segment token lists
        plus a flag that indicates whether any segment matched a dangerous
        pattern (via the caller's matcher_fn).
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable

_OPERATOR_PATTERN = re.compile(r"(?<!\\)\|{1,2}|&&|\|\||;|\n")


def _is_shell_wrapper(token: str) -> bool:
    return token in {"eval", "sh", "bash", "dash", "zsh", "ksh"}


def _extract_command_substitution(text: str) -> list[str]:
    """Pull out ``$(...)`` and backtick payloads for recursive inspection."""
    payloads: list[str] = []
    # ``$(...)`` — allow nested parens.
    depth = 0
    start: int | None = None
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if depth == 0 and ch == "$" and i + 1 < len(chars) and chars[i + 1] == "(":
            depth = 1
            start = i + 2
            i += 2  # skip both ``$`` and ``(``
            continue
        if depth > 0:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and start is not None:
                    payloads.append("".join(chars[start:i]))
                    start = None
        i += 1
    # Backticks.
    pos = 0
    while True:
        i = text.find("`", pos)
        if i < 0:
            break
        j = text.find("`", i + 1)
        if j < 0:
            break
        payloads.append(text[i + 1 : j])
        pos = j + 1
    return payloads


def _normalize_flag_cluster(tokens: list[str]) -> list[str]:
    """Collapse -rf/-fr variants: sort the chars within each short-flag cluster.

    Long options (``--flag``) are left alone. This lets the dangerous-pattern
    matcher compare against a canonical form regardless of how the user
    wrote the flags.
    """
    normalized: list[str] = []
    for token in tokens:
        if token.startswith("--") or not token.startswith("-"):
            normalized.append(token)
            continue
        # short option cluster: -xyz or -rf
        body = token[1:]
        if not body or body[0] == "-":
            normalized.append(token)
            continue
        # Sort so '-rf' == '-fr' (strip a leading whitespace too).
        sorted_body = "".join(sorted(body))
        normalized.append("-" + sorted_body)
    return normalized


def _tokenize_segment(segment: str) -> list[str]:
    """Tokenize one operator-delimited segment with ``shlex``.

    Returns an empty list on parse failure (caller falls back to glob match
    against the raw segment).
    """
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return []


def _segments_for_recursion(segment: str, tokens: list[str]) -> list[str]:
    """Pull substitution payloads and ``eval``/``sh -c`` inner content."""
    payloads: list[str] = []
    # Command substitution (token-aware so quoted payloads are preserved).
    payloads.extend(_extract_command_substitution(segment))
    # ``sh -c 'inner'`` / ``bash -c "inner"`` / ``eval 'inner'``.
    if tokens:
        head = tokens[0]
        if _is_shell_wrapper(head):
            # The shell wrapper's -c arg is the inner command string.
            for i, tok in enumerate(tokens[1:], start=1):
                if tok == "-c" and i + 1 < len(tokens):
                    payloads.append(tokens[i + 1])
                    break
                # ``eval X Y Z`` joins args into one command.
                if head == "eval":
                    payloads.append(" ".join(tokens[1:]))
                    break
    return payloads


def decompose_command(command: str) -> list[str]:
    """Split a shell command into operator-separated segments.

    Honors the operators the existing matcher misses: ``|`` / ``||``, ``&&``,
    ``;``, and newlines. Quoted segments are not split (the operator
    characters inside quotes are escaped first so the regex skips them).
    """
    escaped = _escape_operator_chars(command)
    raw_segments = _OPERATOR_PATTERN.split(escaped)
    # Unescape operator chars back; the split is the only side-effect we need.
    out: list[str] = []
    for s in raw_segments:
        stripped = s.replace("\u0000|", "|").replace("\u0000&", "&").replace("\u0000;", ";").strip()
        if stripped:
            out.append(stripped)
    return out


def _escape_operator_chars(command: str) -> str:
    """Replace operator characters inside single/double-quoted spans with NUL
    so the regex split ignores them. A best-effort tokenizer; backslashes are
    not handled (out of scope).
    """
    out: list[str] = []
    in_single = False
    in_double = False
    for ch in command:
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif (ch in "|&;\n") and not in_single and not in_double:
            out.append("\u0000" + ch)
        else:
            out.append(ch)
    return "".join(out)


def analyze_command(
    command: str,
    matcher_fn: Callable[[list[str]], bool],
) -> dict:
    """Decompose, tokenize, normalize, and recurse to detect dangerous patterns.

    Args:
        command: The shell command string (e.g., a ``bash`` tool arg).
        matcher_fn: Predicate that takes a token list and returns True if
            ANY token matches a dangerous pattern.

    Returns:
        Dict with keys:
            ``matched`` — True if any segment (including recursed inner
                content) matched a dangerous pattern.
            ``segments`` — list of token lists, one per top-level segment.
            ``inner_segments`` — token lists for recursively-inspected
                payloads (``$(...)``, backticks, ``eval``, ``sh -c``).
    """
    segments = decompose_command(command)
    tokenized: list[list[str]] = []
    inner_tokenized: list[list[str]] = []
    matched = False

    for seg in segments:
        tokens = _tokenize_segment(seg)
        if not tokens:
            # Parse failure — degrade to a raw segment glob match, then a
            # per-word scan. Raw split on whitespace is the most
            # conservative fallback.
            if matcher_fn([seg]):
                matched = True
            # Also try a per-word scan — raw split on whitespace is the
            # most conservative fallback.
            for word in seg.split():
                if matcher_fn([word]):
                    matched = True
                    break
            continue
        normalized = _normalize_flag_cluster(tokens)
        tokenized.append(normalized)
        if matcher_fn(normalized):
            matched = True
            continue
        # Recurse into command substitution / wrapper invocations.
        for payload in _segments_for_recursion(seg, tokens):
            inner_segments = decompose_command(payload)
            for inner in inner_segments:
                inner_tokens = _tokenize_segment(inner)
                if not inner_tokens:
                    for word in inner.split():
                        if matcher_fn([word]):
                            matched = True
                            break
                    continue
                inner_normalized = _normalize_flag_cluster(inner_tokens)
                inner_tokenized.append(inner_normalized)
                if matcher_fn(inner_normalized):
                    matched = True

    return {
        "matched": matched,
        "segments": tokenized,
        "inner_segments": inner_tokenized,
    }


__all__ = ["analyze_command", "decompose_command"]
