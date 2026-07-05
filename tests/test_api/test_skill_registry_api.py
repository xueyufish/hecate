"""Tests for SkillRegistry API endpoints."""

from __future__ import annotations

from hecate.api.management.skill_registry import router


def test_skill_registry_router_exists() -> None:
    """Test that SkillRegistry router is importable."""
    assert router is not None
    assert router.prefix == "/api/skills"


def test_skill_registry_router_tags() -> None:
    """Test that SkillRegistry router has correct tags."""
    assert "skill-registry" in router.tags
