"""Tests for ToolMatcher."""

from __future__ import annotations

from hecate.engine.tool_matcher import ToolMatcher


def test_match_none_matches_all() -> None:
    """None pattern matches all tools."""
    assert ToolMatcher.match("web_search", None) is True
    assert ToolMatcher.match("bash", None) is True


def test_match_empty_matches_all() -> None:
    """Empty pattern matches all tools."""
    assert ToolMatcher.match("web_search", "") is True


def test_match_star_matches_all() -> None:
    """Wildcard '*' matches all tools."""
    assert ToolMatcher.match("web_search", "*") is True


def test_match_exact_name() -> None:
    """Exact name matches only that tool."""
    assert ToolMatcher.match("web_search", "web_search") is True
    assert ToolMatcher.match("bash", "web_search") is False


def test_match_pipe_separated() -> None:
    """Pipe-separated pattern matches any listed name."""
    pattern = "Edit|Write"
    assert ToolMatcher.match("Edit", pattern) is True
    assert ToolMatcher.match("Write", pattern) is True
    assert ToolMatcher.match("Bash", pattern) is False


def test_match_regex() -> None:
    """Regex pattern matches via re.match."""
    pattern = r"mcp__github__.*"
    assert ToolMatcher.match("mcp__github__create_issue", pattern) is True
    assert ToolMatcher.match("mcp__github__list_prs", pattern) is True
    assert ToolMatcher.match("mcp__slack__send", pattern) is False
    assert ToolMatcher.match("web_search", pattern) is False


def test_match_invalid_regex_returns_false() -> None:
    """Invalid regex pattern returns False."""
    assert ToolMatcher.match("test", "[invalid") is False


def test_compile_always_matcher() -> None:
    """Compiled None/empty/* pattern matches everything."""
    m1 = ToolMatcher.compile(None)
    m2 = ToolMatcher.compile("")
    m3 = ToolMatcher.compile("*")
    assert m1.match("anything") is True
    assert m2.match("anything") is True
    assert m3.match("anything") is True


def test_compile_exact_matcher() -> None:
    """Compiled exact pattern matches only listed names."""
    m = ToolMatcher.compile("Edit|Write")
    assert m.match("Edit") is True
    assert m.match("Write") is True
    assert m.match("Bash") is False


def test_compile_regex_matcher() -> None:
    """Compiled regex pattern matches via re.match."""
    m = ToolMatcher.compile(r"mcp__.*")
    assert m.match("mcp__github__list") is True
    assert m.match("bash") is False


def test_compile_invalid_regex_returns_never() -> None:
    """Compiled invalid regex pattern matches nothing."""
    m = ToolMatcher.compile("[invalid")
    assert m.match("anything") is False


def test_case_sensitive() -> None:
    """Matching is case-sensitive."""
    assert ToolMatcher.match("Edit", "edit") is False
    assert ToolMatcher.match("edit", "Edit") is False


def test_match_mcp_tools_with_complex_regex() -> None:
    """Complex MCP tool name regex works correctly."""
    pattern = r"mcp__(github|slack)__.*"
    assert ToolMatcher.match("mcp__github__create_issue", pattern) is True
    assert ToolMatcher.match("mcp__slack__send_message", pattern) is True
    assert ToolMatcher.match("mcp__jira__create", pattern) is False
