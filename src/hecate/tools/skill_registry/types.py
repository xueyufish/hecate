"""Skill Registry type definitions.

Defines the unified skill abstraction that unifies Tools, Skills, Knowledge
Bases, Workflows, and Agents as a single SkillRef type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class SkillRefType(StrEnum):
    """Types of skill references that can be resolved by SkillRegistry."""

    TOOL = "tool"
    SKILL = "skill"
    KNOWLEDGE = "knowledge"
    WORKFLOW = "workflow"
    AGENT = "agent"
    REMOTE_AGENT = "remote_agent"


@dataclass(frozen=True)
class SkillRef:
    """A reference to a skill that can be resolved by SkillRegistry.

    Attributes:
        ref_type: The type of skill being referenced.
        ref_id: The identifier (name for tools/skills, UUID for KB/workflow/agent, URL for remote).
    """

    ref_type: SkillRefType
    ref_id: str | UUID

    def __post_init__(self) -> None:
        """Normalize ref_id to string for consistent comparison."""
        if isinstance(self.ref_id, UUID):
            object.__setattr__(self, "ref_id", str(self.ref_id))


@dataclass
class ResolvedSkill:
    """A resolved skill with uniform metadata regardless of underlying resource type.

    Attributes:
        name: Human-readable skill name.
        description: What the skill does.
        source: The original resource type.
        parameters: JSON Schema for the skill's input parameters (tools/workflows).
        metadata: Additional type-specific metadata.
    """

    name: str
    description: str
    source: SkillRefType
    parameters: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SkillNotFoundError(Exception):
    """Raised when a SkillRef cannot be resolved to an existing resource."""

    def __init__(self, ref_type: SkillRefType, ref_id: str | UUID) -> None:
        self.ref_type = ref_type
        self.ref_id = ref_id
        super().__init__(f"Skill not found: {ref_type}={ref_id}")
