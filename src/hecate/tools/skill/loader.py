"""SkillLoader service for two-level skill loading and formatting.

Resolves an agent's skill names to SkillModel records from the database and
serves them at two levels (progressive disclosure):

- **L1 catalog** — name + description per bound skill, injected into the
  system prompt so the model knows what is available without paying the
  full token cost.
- **L2 content** — full SKILL.md instructions, loaded on demand via the
  ``load_skill`` built-in tool (run-scoped context, never appended to the
  system prompt).

Skills with ``auto_load=True`` keep their legacy semantics: full content is
always injected without an L2 request. Set
``settings.SKILL_PROGRESSIVE_DISCLOSURE = False`` to restore the legacy
inject-everything behaviour wholesale (rollout escape hatch).
"""

from __future__ import annotations

import logging
import uuid
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.evolution import SkillUsageEventModel
from hecate.models.plugin import PluginModel
from hecate.models.skill import SkillModel

logger = logging.getLogger(__name__)

# Default total token budget for all skills combined
DEFAULT_TOTAL_TOKEN_BUDGET = 4000

# Default token budget for the L1 catalog block (name + description entries)
DEFAULT_CATALOG_TOKEN_BUDGET = 1000

# Per-entry description cap inside the L1 catalog (~100 tokens)
DESCRIPTION_CHAR_BUDGET = 400

# Rough token estimation: 1 token ≈ 4 characters
CHARS_PER_TOKEN = 4

_SKILL_CATALOG_HINT = (
    "Call the load_skill tool with a skill's name to load its full instructions before applying that skill."
)


class SkillNotAdvertisedError(Exception):
    """Requested skill is not advertised in the agent's L1 catalog."""


class SkillLoader:
    """Loads agent skills from DB and formats them for context injection.

    Progressive mode (default) returns an L1 catalog from ``format_skills``
    and full content from ``load_skill_content``. Legacy mode injects every
    skill's full content from ``format_skills`` (pre-change behaviour).
    """

    def __init__(self, db: AsyncSession, *, progressive: bool | None = None) -> None:
        self._db = db
        if progressive is None:
            from hecate.core.config import settings

            progressive = settings.SKILL_PROGRESSIVE_DISCLOSURE
        self._progressive = progressive

    async def format_skills(
        self,
        agent_id: UUID,
        workspace_id: UUID,
        total_budget: int = DEFAULT_TOTAL_TOKEN_BUDGET,
        catalog_budget: int = DEFAULT_CATALOG_TOKEN_BUDGET,
        session_id: UUID | None = None,
    ) -> str:
        """Load and format skills for an agent's system prompt.

        Args:
            agent_id: UUID of the agent.
            workspace_id: UUID of the workspace (for multi-tenant isolation).
            total_budget: Maximum total tokens for injected skill content.
            catalog_budget: Maximum tokens for the L1 catalog (progressive mode).

        Returns:
            Formatted XML string (L1 catalog, or full blocks in legacy mode);
            empty string if the agent has no usable skills.
        """
        agent = await self._load_agent(agent_id)
        if agent is None:
            logger.warning("Agent %s not found for skill loading", agent_id)
            return ""

        explicit_names: list[str] = agent.skills or []

        # Query auto_load skills in workspace
        auto_load_skills = await self._query_auto_load_skills(workspace_id)
        auto_load_names = [s.name for s in auto_load_skills]

        if not self._progressive:
            return await self._format_legacy(
                agent_id,
                workspace_id,
                explicit_names,
                auto_load_skills,
                auto_load_names,
                total_budget,
            )

        # Merge and deduplicate (explicit first, then auto_load)
        all_names = list(dict.fromkeys(explicit_names + auto_load_names))
        skills = await self._load_skills_by_names(all_names, workspace_id)
        found = {s.name for s in skills}
        for name in all_names:
            if name not in found:
                logger.warning(
                    "Skill '%s' referenced by agent %s not found in workspace %s",
                    name,
                    agent_id,
                    workspace_id,
                )
        if not skills:
            return ""

        skills_by_name = {s.name: s for s in skills}

        # auto_load skills keep full injection; explicit skills become L1 entries
        auto_blocks: list[tuple[str, str]] = []
        for name in auto_load_names:
            skill = skills_by_name.get(name)
            if skill is None:
                continue
            text = self._format_single_skill(skill)
            if len(text) // CHARS_PER_TOKEN > skill.max_tokens:
                text = self._truncate_to_tokens(text, skill.max_tokens)
            auto_blocks.append((name, text))
        auto_blocks = self._enforce_total_budget(auto_blocks, total_budget, protected={name for name, _ in auto_blocks})
        auto_loaded = {name for name, _ in auto_blocks}

        catalog_entries: list[tuple[str, str]] = []
        for name in explicit_names:
            if name in auto_loaded:
                continue
            skill = skills_by_name.get(name)
            if skill is None or skill.auto_load:
                continue
            description = skill.description[:DESCRIPTION_CHAR_BUDGET]
            catalog_entries.append((name, f'<skill name="{skill.name}" description="{description}"/>'))
        catalog_entries = self._enforce_catalog_budget(catalog_entries, catalog_budget)

        if not catalog_entries and not auto_blocks:
            return ""

        parts: list[str] = ["<skills>"]
        parts.extend(text for _, text in catalog_entries)
        parts.extend(text for _, text in auto_blocks)
        if catalog_entries:
            parts.append(f"[Skills usage]: {_SKILL_CATALOG_HINT}")
        parts.append("</skills>")

        await self._record_usage(
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_id=session_id,
            skills=skills,
            event_type="catalog_served",
        )
        return "\n".join(parts)

    async def load_skill_content(
        self,
        skill_name: str,
        agent_id: UUID,
        workspace_id: UUID,
        session_id: UUID | None = None,
    ) -> str:
        """Load full L2 content for an advertised skill (run-scoped context).

        Args:
            skill_name: Name of the skill to load.
            agent_id: UUID of the requesting agent (catalog membership check).
            workspace_id: UUID of the workspace (multi-tenant isolation).

        Returns:
            Formatted full skill block, truncated to the skill's max_tokens.

        Raises:
            SkillNotAdvertisedError: The skill is not bound to the agent, is
                missing from the workspace, or its owning plugin is disabled.
        """
        agent = await self._load_agent(agent_id)
        if agent is None:
            raise SkillNotAdvertisedError(f"Agent {agent_id} not found")

        advertised = agent.skills or []
        if skill_name not in advertised:
            raise SkillNotAdvertisedError(
                f"Skill '{skill_name}' is not advertised for agent {agent_id}. Available skills: {advertised or 'none'}"
            )

        skills = await self._load_skills_by_names([skill_name], workspace_id)
        if not skills:
            raise SkillNotAdvertisedError(f"Skill '{skill_name}' not found in workspace {workspace_id}")
        skill = skills[0]

        text = self._format_single_skill(skill)
        if len(text) // CHARS_PER_TOKEN > skill.max_tokens:
            text = self._truncate_to_tokens(text, skill.max_tokens)

        await self._record_usage(
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_id=session_id,
            skills=[skill],
            event_type="skill_loaded",
        )
        return text

    async def _format_legacy(
        self,
        agent_id: UUID,
        workspace_id: UUID,
        explicit_names: list[str],
        auto_load_skills: list[SkillModel],
        auto_load_names: list[str],
        total_budget: int,
    ) -> str:
        """Legacy behaviour: inject every bound skill's full content."""
        all_names = list(dict.fromkeys(explicit_names + auto_load_names))
        if not all_names:
            return ""

        skills = await self._load_skills_by_names(all_names, workspace_id)
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

        truncated: list[tuple[str, str]] = []
        for skill in skills:
            text = self._format_single_skill(skill)
            est_tokens = len(text) // CHARS_PER_TOKEN
            if est_tokens > skill.max_tokens:
                text = self._truncate_to_tokens(text, skill.max_tokens)
            truncated.append((skill.name, text))

        truncated = self._enforce_total_budget(truncated, total_budget, protected={s.name for s in auto_load_skills})
        if not truncated:
            return ""

        parts = ["<skills>"]
        parts.extend(text for _, text in truncated)
        parts.append("</skills>")
        return "\n".join(parts)

    async def _load_agent(self, agent_id: UUID) -> AgentModel | None:
        """Load agent from DB by ID."""
        result = await self._db.execute(
            select(AgentModel).where(
                AgentModel.id == agent_id,
                ~AgentModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def _query_auto_load_skills(self, workspace_id: UUID) -> list[SkillModel]:
        """Query all skills with auto_load=True in workspace.

        Plugin-derived skills load only while their owning package is
        enabled (the package enable bit is the single source of truth).
        """
        result = await self._db.execute(
            select(SkillModel)
            .outerjoin(PluginModel, SkillModel.plugin_id == PluginModel.id)
            .where(
                SkillModel.workspace_id == workspace_id,
                SkillModel.auto_load.is_(True),
                ~SkillModel.deleted,
                self._plugin_enabled_condition(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def _plugin_enabled_condition():
        """Plugin-sourced skills visible only when their package is enabled."""
        return or_(
            SkillModel.plugin_id.is_(None),
            (PluginModel.status == "enabled") & PluginModel.deleted_at.is_(None),
        )

    async def _load_skills_by_names(
        self,
        names: list[str],
        workspace_id: UUID,
    ) -> list[SkillModel]:
        """Load skills by name within workspace (including system skills).

        Plugin-derived skills are skipped (like missing skills) when their
        owning package is disabled or soft-deleted.
        """
        zero_uuid = uuid.UUID(int=0)
        result = await self._db.execute(
            select(SkillModel)
            .outerjoin(PluginModel, SkillModel.plugin_id == PluginModel.id)
            .where(
                SkillModel.name.in_(names),
                SkillModel.workspace_id.in_([workspace_id, zero_uuid]),
                ~SkillModel.deleted,
                self._plugin_enabled_condition(),
            )
        )
        return list(result.scalars().all())

    def _format_single_skill(self, skill: SkillModel) -> str:
        """Format a single skill as full XML block (L2 content)."""
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

    def _enforce_catalog_budget(
        self,
        entries: list[tuple[str, str]],
        catalog_budget: int,
    ) -> list[tuple[str, str]]:
        """Drop catalog entries from the end until the catalog fits its budget."""
        budget_chars = catalog_budget * CHARS_PER_TOKEN
        result: list[tuple[str, str]] = []
        used = 0
        for name, text in entries:
            if used + len(text) > budget_chars:
                logger.info(
                    "Skill catalog budget exceeded; dropping skill '%s' from L1 catalog",
                    name,
                )
                continue
            result.append((name, text))
            used += len(text)
        return result

    def _enforce_total_budget(
        self,
        items: list[tuple[str, str]],
        total_budget: int,
        protected: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """Keep protected (auto_load) skills first, then fill remaining budget.

        On overflow, unprotected skills that no longer fit are dropped in
        declaration order.
        """
        total_chars = sum(len(text) for _, text in items)
        budget_chars = total_budget * CHARS_PER_TOKEN

        if total_chars <= budget_chars:
            return items

        protected = protected or set()
        protected_items = [(n, t) for n, t in items if n in protected]
        other_items = [(n, t) for n, t in items if n not in protected]

        result = list(protected_items)
        remaining = budget_chars - sum(len(t) for _, t in result)
        for name, text in other_items:
            if len(text) <= remaining:
                result.append((name, text))
                remaining -= len(text)
            else:
                logger.info(
                    "Skill total budget exceeded; dropping skill '%s' from injection",
                    name,
                )
        return result

    async def _record_usage(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        session_id: UUID | None,
        skills: list[SkillModel],
        event_type: str,
    ) -> None:
        """Record skill usage events; best-effort inside a savepoint.

        A failure rolls back only the savepoint, never the caller's
        transaction (mirrors the best-effort contract of the evidence
        tracker and security finding writer).
        """
        try:
            async with self._db.begin_nested():
                for skill in skills:
                    self._db.add(
                        SkillUsageEventModel(
                            workspace_id=workspace_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            skill_id=skill.id,
                            skill_name=skill.name,
                            event_type=event_type,
                        )
                    )
                await self._db.flush()
        except Exception:
            logger.warning("Failed to record skill usage event", exc_info=True)
