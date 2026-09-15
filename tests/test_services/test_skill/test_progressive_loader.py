"""Tests for two-level (progressive disclosure) skill loading.

Covers the skill-loader delta of 1.3.6f: L1 catalog injection, L2 on-demand
loading with catalog-membership enforcement, budget behaviour, legacy
escape hatch, and usage event recording.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import SkillUsageEventModel
from hecate.models.skill import SkillModel
from hecate.tools.skill.loader import (
    SkillLoader,
    SkillNotAdvertisedError,
)


async def _create_skill(
    db: AsyncSession,
    name: str,
    instructions: str = "Test instructions",
    workspace_id: uuid.UUID | None = None,
    auto_load: bool = False,
    max_tokens: int = 2000,
) -> SkillModel:
    skill = SkillModel(
        workspace_id=workspace_id or uuid.UUID(int=0),
        name=name,
        description=f"Description for {name}",
        source="user",
        instructions=instructions,
        auto_load=auto_load,
        max_tokens=max_tokens,
    )
    db.add(skill)
    await db.flush()
    return skill


async def _create_agent(
    db: AsyncSession,
    skills: list[str],
    workspace_id: uuid.UUID | None = None,
):
    from hecate.models.agent import AgentModel

    agent = AgentModel(
        workspace_id=workspace_id or uuid.UUID(int=0),
        name="test-agent",
        persona="You are helpful.",
        model_config_db={"model": "gpt-4o"},
        skills=skills,
    )
    db.add(agent)
    await db.flush()
    return agent


class TestL1Catalog:
    async def test_catalog_omits_instructions(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "code-review", "SECRET-INSTRUCTIONS")
        agent = await _create_agent(db_session, ["code-review"])

        loader = SkillLoader(db_session, progressive=True)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert '<skill name="code-review" description="Description for code-review"/>' in result
        assert "SECRET-INSTRUCTIONS" not in result
        assert "load_skill" in result

    async def test_auto_load_keeps_full_injection_without_hint(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "always-on", "FULL-CONTENT", auto_load=True)
        agent = await _create_agent(db_session, [])

        loader = SkillLoader(db_session, progressive=True)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert "FULL-CONTENT" in result
        assert "load_skill" not in result

    async def test_explicit_skill_bound_as_auto_load_is_not_duplicated(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "shared", "FULL-CONTENT", auto_load=True)
        agent = await _create_agent(db_session, ["shared"])

        loader = SkillLoader(db_session, progressive=True)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert result.count('<skill name="shared"') == 1
        assert "FULL-CONTENT" in result

    async def test_catalog_budget_drops_entries(self, db_session: AsyncSession) -> None:
        for i in range(5):
            await _create_skill(db_session, f"skill-{i}", f"content {i}")
        agent = await _create_agent(db_session, [f"skill-{i}" for i in range(5)])

        loader = SkillLoader(db_session, progressive=True)
        result = await loader.format_skills(agent.id, agent.workspace_id, catalog_budget=1)

        # 1 token ≈ 4 chars — nothing fits, everything dropped
        assert '<skill name="skill-0"' not in result

    async def test_usage_event_recorded_on_catalog_serve(self, db_session: AsyncSession) -> None:
        skill = await _create_skill(db_session, "tracked-skill")
        agent = await _create_agent(db_session, ["tracked-skill"])

        loader = SkillLoader(db_session, progressive=True)
        await loader.format_skills(agent.id, agent.workspace_id)

        rows = (
            (
                await db_session.execute(
                    select(SkillUsageEventModel).where(SkillUsageEventModel.skill_name == "tracked-skill")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].event_type == "catalog_served"
        assert rows[0].skill_id == skill.id


class TestL2LoadSkillContent:
    async def test_load_returns_full_content(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "code-review", "FULL-INSTRUCTIONS")
        agent = await _create_agent(db_session, ["code-review"])

        loader = SkillLoader(db_session, progressive=True)
        content = await loader.load_skill_content("code-review", agent.id, agent.workspace_id)

        assert '<skill name="code-review">' in content
        assert "FULL-INSTRUCTIONS" in content

    async def test_load_rejects_unadvertised_skill(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "other-skill", "content")
        agent = await _create_agent(db_session, ["bound-skill"])

        loader = SkillLoader(db_session, progressive=True)
        with pytest.raises(SkillNotAdvertisedError, match="not advertised"):
            await loader.load_skill_content("other-skill", agent.id, agent.workspace_id)

    async def test_load_rejects_missing_skill(self, db_session: AsyncSession) -> None:
        agent = await _create_agent(db_session, ["ghost-skill"])

        loader = SkillLoader(db_session, progressive=True)
        with pytest.raises(SkillNotAdvertisedError, match="not found"):
            await loader.load_skill_content("ghost-skill", agent.id, agent.workspace_id)

    async def test_load_truncates_to_max_tokens(self, db_session: AsyncSession) -> None:
        long_instructions = "Paragraph one. " * 500
        await _create_skill(db_session, "big-skill", long_instructions, max_tokens=100)
        agent = await _create_agent(db_session, ["big-skill"])

        loader = SkillLoader(db_session, progressive=True)
        content = await loader.load_skill_content("big-skill", agent.id, agent.workspace_id)

        assert len(content) <= 100 * 4 + 100  # truncation + formatting slack

    async def test_load_records_usage_event(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "counted-skill")
        agent = await _create_agent(db_session, ["counted-skill"])

        loader = SkillLoader(db_session, progressive=True)
        await loader.load_skill_content("counted-skill", agent.id, agent.workspace_id)

        rows = (
            (
                await db_session.execute(
                    select(SkillUsageEventModel).where(SkillUsageEventModel.event_type == "skill_loaded")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].agent_id == agent.id


class TestLegacyEscapeHatch:
    async def test_progressive_false_restores_full_injection(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "legacy-skill", "FULL-INSTRUCTIONS")
        agent = await _create_agent(db_session, ["legacy-skill"])

        loader = SkillLoader(db_session, progressive=False)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert '<skill name="legacy-skill">' in result
        assert "FULL-INSTRUCTIONS" in result

    async def test_legacy_mode_budget_keeps_auto_load_first(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "auto-a", "content", auto_load=True)
        await _create_skill(db_session, "plain-b", "content")
        agent = await _create_agent(db_session, ["auto-a", "plain-b"])

        loader = SkillLoader(db_session, progressive=False)
        result = await loader.format_skills(agent.id, agent.workspace_id, total_budget=1)

        assert '<skill name="auto-a">' in result
        assert '<skill name="plain-b">' not in result
