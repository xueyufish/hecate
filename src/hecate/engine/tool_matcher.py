"""Tool name matcher for hook filtering.

Supports Claude Code compatible matcher syntax:
- Plain alphanumerics + `|` → exact or pipe-separated match
- Regex special chars → re.match
- None/empty/`*` → match all tools

Design: pre-compile regex patterns at construction time for performance.
"""

from __future__ import annotations

import re


class ToolMatcher:
    """Evaluate tool name against matcher patterns.

    Call ``match(tool_name, pattern)`` for stateless matching.
    For repeated matching with the same pattern, create a compiled matcher
    via ``compile(pattern)`` and call ``compiled.match(tool_name)``.
    """

    @staticmethod
    def match(tool_name: str, pattern: str | None) -> bool:
        """Check if tool_name matches the given pattern.

        Args:
            tool_name: The tool name to test.
            pattern: Matcher pattern. None/empty/"*" matches everything.
                Pipe-separated names ("Edit|Write") match any listed name.
                Regex special chars trigger regex matching.

        Returns:
            True if the tool name matches the pattern.
        """
        if not pattern or pattern == "*":
            return True

        if ToolMatcher._is_plain(pattern):
            parts = [p.strip() for p in pattern.split("|")]
            return tool_name in parts

        try:
            return bool(re.match(pattern, tool_name))
        except re.error:
            return False

    @staticmethod
    def compile(pattern: str | None) -> _CompiledMatcher:
        """Pre-compile a matcher pattern for repeated use.

        Args:
            pattern: Matcher pattern string.

        Returns:
            A _CompiledMatcher with a match(tool_name) method.
        """
        if not pattern or pattern == "*":
            return _AlwaysMatcher()

        if ToolMatcher._is_plain(pattern):
            parts = frozenset(p.strip() for p in pattern.split("|"))
            return _ExactMatcher(parts)

        try:
            regex = re.compile(pattern)
            return _RegexMatcher(regex)
        except re.error:
            return _NeverMatcher()

    @staticmethod
    def _is_plain(pattern: str) -> bool:
        """Check if pattern is plain (no regex special chars)."""
        regex_specials = set(r".^$*+?{}[]\|()")
        return not any(c in regex_specials for c in pattern)


class _CompiledMatcher:
    """Base class for compiled matchers."""

    def match(self, tool_name: str) -> bool:
        raise NotImplementedError


class _AlwaysMatcher(_CompiledMatcher):
    """Matches everything."""

    def match(self, tool_name: str) -> bool:
        return True


class _NeverMatcher(_CompiledMatcher):
    """Matches nothing (invalid pattern)."""

    def match(self, tool_name: str) -> bool:
        return False


class _ExactMatcher(_CompiledMatcher):
    """Matches against a set of exact names."""

    def __init__(self, names: frozenset[str]) -> None:
        self._names = names

    def match(self, tool_name: str) -> bool:
        return tool_name in self._names


class _RegexMatcher(_CompiledMatcher):
    """Matches against a compiled regex."""

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self._pattern = pattern

    def match(self, tool_name: str) -> bool:
        return bool(self._pattern.match(tool_name))
