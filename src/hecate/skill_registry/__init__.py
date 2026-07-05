"""Unified Skill Registry for Hecate.

Provides a single abstraction for resolving, invoking, and formatting
heterogeneous skill types (Tools, Skills, Knowledge Bases, Workflows,
Agents) without data migration.
"""

from hecate.skill_registry.registry import SkillRegistry
from hecate.skill_registry.types import (
    ResolvedSkill,
    SkillNotFoundError,
    SkillRef,
    SkillRefType,
)

__all__ = [
    "ResolvedSkill",
    "SkillNotFoundError",
    "SkillRef",
    "SkillRefType",
    "SkillRegistry",
]
