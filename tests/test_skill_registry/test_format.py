"""Tests for SkillRegistry.format_for_llm() — XML formatting."""

from __future__ import annotations

from hecate.skill_registry.registry import SkillRegistry
from hecate.skill_registry.types import ResolvedSkill, SkillRefType


def test_format_empty_list() -> None:
    """Test formatting an empty skill list returns empty string."""
    registry = SkillRegistry.__new__(SkillRegistry)
    result = registry.format_for_llm([])
    assert result == ""


def test_format_single_tool() -> None:
    """Test formatting a single tool skill."""
    registry = SkillRegistry.__new__(SkillRegistry)
    skills = [
        ResolvedSkill(
            name="search",
            description="Search the web",
            source=SkillRefType.TOOL,
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
    ]
    result = registry.format_for_llm(skills)
    assert "<skills>" in result
    assert 'name="search"' in result
    assert 'type="tool"' in result
    assert "Search the web" in result
    assert "</skills>" in result


def test_format_multiple_skills() -> None:
    """Test formatting multiple skills of different types."""
    registry = SkillRegistry.__new__(SkillRegistry)
    skills = [
        ResolvedSkill(name="search", description="Search", source=SkillRefType.TOOL),
        ResolvedSkill(name="docs", description="Knowledge base", source=SkillRefType.KNOWLEDGE),
        ResolvedSkill(name="pipeline", description="Data pipeline", source=SkillRefType.WORKFLOW),
    ]
    result = registry.format_for_llm(skills)
    assert result.count("<skill name=") == 3
    assert 'type="tool"' in result
    assert 'type="knowledge"' in result
    assert 'type="workflow"' in result


def test_format_with_metadata() -> None:
    """Test that metadata values are included in XML output."""
    registry = SkillRegistry.__new__(SkillRegistry)
    skills = [
        ResolvedSkill(
            name="agent",
            description="Helper agent",
            source=SkillRefType.AGENT,
            metadata={"mode": "chat", "risk_level": "LOW"},
        )
    ]
    result = registry.format_for_llm(skills)
    assert "<mode>chat</mode>" in result
    assert "<risk_level>LOW</risk_level>" in result
