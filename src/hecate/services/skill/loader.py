"""SkillLoader service for loading and formatting agent skills.

Resolves an agent's skill names to SkillModel records from the database,
formats them as XML-tagged context blocks, and handles auto_load and
max_tokens budget.
"""

from __future__ import annotations

import logging
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.skill import SkillModel

logger = logging.getLogger(__name__)

# Default total token budget for all skills combined
DEFAULT_TOTAL_TOKEN_BUDGET = 4000

# Rough token estimation: 1 token ≈ 4 characters
CHARS_PER_TOKEN = 4


class SkillLoader:
    """Loads agent skills from DB and formats them for system prompt injection.

    Uses XML format: ``<skills><skill name="...">body</skill></skills>``
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def format_skills(
        self,
        agent_id: UUID,
        workspace_id: UUID,
        total_budget: int = DEFAULT_TOTAL_TOKEN_BUDGET,
    ) -> str:
        """Load and format skills for an agent's system prompt.

        Args:
            agent_id: UUID of the agent.
            workspace_id: UUID of the workspace (for multi-tenant isolation).
            total_budget: Maximum total tokens for all skills combined.

        Returns:
            Formatted XML string with all skills, or empty string if none.
        """
        agent = await self._load_agent(agent_id)
        if agent is None:
            logger.warning("Agent %s not found for skill loading", agent_id)
            return ""

        explicit_names: list[str] = agent.skills or []

        # Query auto_load skills in workspace
        auto_load_skills = await self._query_auto_load_skills(workspace_id)
        auto_load_names = [s.name for s in auto_load_skills]

        # Merge and deduplicate (explicit first, then auto_load)
        all_names = list(dict.fromkeys(explicit_names + auto_load_names))

        if not all_names:
            return ""

        # Load all referenced skills from DB
        skills = await self._load_skills_by_names(all_names, workspace_id)

        # Log warnings for missing skills
        found_names = {s.name for s in skills}
        for name in all_names:
            if name not in found_names:
                logger.warning(
                    "Skill '%s' referenced by agent %s not found in workspace %s",
                    name,
                    agent_id,
                    workspace_id,
                )

        if not skills:
            return ""

        # Truncate individual skills to their max_tokens
        truncated: list[tuple[str, str]] = []
        for skill in skills:
            text = self._format_single_skill(skill)
            est_tokens = len(text) // CHARS_PER_TOKEN
            if est_tokens > skill.max_tokens:
                text = self._truncate_to_tokens(text, skill.max_tokens)
            truncated.append((skill.name, text))

        # Enforce total budget
        truncated = self._enforce_total_budget(truncated, skills, total_budget)

        if not truncated:
            return ""

        # Format as XML
        parts = ["<skills>"]
        for _name, text in truncated:
            parts.append(text)
        parts.append("</skills>")

        return "\n".join(parts)

    async def _load_agent(self, agent_id: UUID) -> AgentModel | None:
        """Load agent from DB by ID."""
        result = await self._db.execute(
            select(AgentModel).where(
                AgentModel.id == agent_id,
                AgentModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _query_auto_load_skills(self, workspace_id: UUID) -> list[SkillModel]:
        """Query all skills with auto_load=True in workspace."""
        result = await self._db.execute(
            select(SkillModel).where(
                SkillModel.workspace_id == workspace_id,
                SkillModel.auto_load.is_(True),
                SkillModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def _load_skills_by_names(
        self,
        names: list[str],
        workspace_id: UUID,
    ) -> list[SkillModel]:
        """Load skills by name within workspace (including system skills)."""
        zero_uuid = uuid.UUID(int=0)
        result = await self._db.execute(
            select(SkillModel).where(
                SkillModel.name.in_(names),
                SkillModel.workspace_id.in_([workspace_id, zero_uuid]),
                SkillModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    def _format_single_skill(self, skill: SkillModel) -> str:
        """Format a single skill as XML block."""
        body = skill.description
        if skill.instructions:
            body += "\n\n" + skill.instructions
        return f'<skill name="{skill.name}">\n{body}\n</skill>'

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to approximately max_tokens at paragraph boundary."""
        max_chars = max_tokens * CHARS_PER_TOKEN
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]
        last_break = truncated.rfind("\n\n")
        if last_break > max_chars // 2:
            return truncated[:last_break]

        last_period = truncated.rfind(". ")
        if last_period > max_chars // 2:
            return truncated[: last_period + 1]

        return truncated

    def _enforce_total_budget(
        self,
        items: list[tuple[str, str]],
        skills: list[SkillModel],
        total_budget: int,
    ) -> list[tuple[str, str]]:
        """Drop non-auto_load skills if total exceeds budget."""
        total_chars = sum(len(text) for _, text in items)
        budget_chars = total_budget * CHARS_PER_TOKEN

        if total_chars <= budget_chars:
            return items

        auto_load_set = {s.name for s in skills if s.auto_load}
        auto_load_items = [(n, t) for n, t in items if n in auto_load_set]
        other_items = [(n, t) for n, t in items if n not in auto_load_set]

        # Start with auto_load skills
        result = list(auto_load_items)
        remaining_budget = budget_chars - sum(len(t) for _, t in result)

        for name, text in other_items:
            if len(text) <= remaining_budget:
                result.append((name, text))
                remaining_budget -= len(text)
            else:
                break

        return result
